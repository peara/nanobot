from __future__ import annotations

import asyncio

import pytest

from nanobot.cancel_token import CancellationToken, LlmCallCancelledError


class TestLlmCallCancelledError:
    def test_default_message(self) -> None:
        err = LlmCallCancelledError()
        assert "cancelled" in str(err).lower()

    def test_with_scope(self) -> None:
        err = LlmCallCancelledError(scope="telegram:123")
        assert "telegram:123" in str(err)

    def test_without_scope(self) -> None:
        err = LlmCallCancelledError()
        assert str(err)  # has a string representation


class TestCancellationTokenBasics:
    def test_initial_state_is_not_cancelled(self) -> None:
        token = CancellationToken()
        assert not token.is_cancelled

    def test_cancel_sets_is_cancelled(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled

    def test_cancel_is_idempotent(self) -> None:
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled


class TestCancellationTokenWait:
    async def test_wait_returns_immediately_if_already_cancelled(self) -> None:
        token = CancellationToken()
        token.cancel()
        # Should return immediately, not hang
        await token.wait()

    async def test_wait_blocks_until_cancelled(self) -> None:
        token = CancellationToken()

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            token.cancel()

        asyncio.create_task(cancel_after_delay())
        await token.wait()
        assert token.is_cancelled


class TestCancellationTokenChildren:
    def test_create_child_linked_to_parent(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        assert child._parent is parent
        assert child in parent._children

    def test_cancel_parent_cascades_to_child(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        parent.cancel()
        assert parent.is_cancelled
        assert child.is_cancelled

    def test_cancel_parent_cascades_to_grandchildren(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        grandchild = child.create_child()
        parent.cancel()
        assert parent.is_cancelled
        assert child.is_cancelled
        assert grandchild.is_cancelled

    def test_cancel_child_does_not_cancel_parent(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        child.cancel()
        assert child.is_cancelled
        assert not parent.is_cancelled

    def test_cancel_child_cascades_to_grandchildren(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        grandchild = child.create_child()
        child.cancel()
        assert child.is_cancelled
        assert grandchild.is_cancelled
        assert not parent.is_cancelled

    def test_child_inherits_cancelled_from_parent(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        parent.cancel()
        # child._event is set because cancel() cascades,
        # but is_cancelled also walks parent chain
        assert child.is_cancelled

    def test_multiple_children_all_cancelled(self) -> None:
        parent = CancellationToken()
        child1 = parent.create_child()
        child2 = parent.create_child()
        child3 = parent.create_child()
        parent.cancel()
        assert child1.is_cancelled
        assert child2.is_cancelled
        assert child3.is_cancelled

    async def test_child_wait_triggered_by_parent_cancel(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            parent.cancel()

        asyncio.create_task(cancel_after_delay())
        await child.wait()
        assert child.is_cancelled
        assert parent.is_cancelled

    async def test_grandchild_wait_triggered_by_parent_cancel(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        grandchild = child.create_child()

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            parent.cancel()

        asyncio.create_task(cancel_after_delay())
        await grandchild.wait()
        assert grandchild.is_cancelled

    async def test_cancel_child_does_not_unblock_parent_wait(self) -> None:
        parent = CancellationToken()
        child = parent.create_child()
        child.cancel()

        # parent.wait() should NOT return just because a child was cancelled
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(parent.wait(), timeout=0.1)

    def test_create_child_after_parent_cancelled(self) -> None:
        parent = CancellationToken()
        parent.cancel()
        # Creating a child AFTER parent is already cancelled
        child = parent.create_child()
        # is_cancelled walks the parent chain, so child is effectively cancelled
        assert child.is_cancelled
        # Child's own event is NOT set (cancel cascade already ran),
        # but is_cancelled returns True via parent inheritance
        assert not child._event.is_set()

    async def test_create_child_after_parent_cancelled_wait_returns(self) -> None:
        parent = CancellationToken()
        parent.cancel()
        child = parent.create_child()
        # wait() should return immediately because parent is cancelled
        await child.wait()


class TestCancellationTokenDeepChains:
    def test_cascading_cancels_long_chain(self) -> None:
        tokens = [CancellationToken()]
        for _ in range(10):
            tokens.append(tokens[-1].create_child())
        tokens[0].cancel()
        assert all(t.is_cancelled for t in tokens)

    def test_mid_chain_cancel(self) -> None:
        root = CancellationToken()
        mid = root.create_child()
        leaf = mid.create_child()
        mid.cancel()
        assert mid.is_cancelled
        assert leaf.is_cancelled
        assert not root.is_cancelled

    async def test_wait_on_deep_chain_triggered_by_root_cancel(self) -> None:
        tokens = [CancellationToken()]
        for _ in range(5):
            tokens.append(tokens[-1].create_child())
        leaf = tokens[-1]

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            tokens[0].cancel()

        asyncio.create_task(cancel_after_delay())
        await leaf.wait()
        assert leaf.is_cancelled
