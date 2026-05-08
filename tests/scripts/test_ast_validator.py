from __future__ import annotations

from nanobot.scripts.validator import NanoScriptAstValidator


def test_ast_validator_rejects_import() -> None:
    code = """
import os

def script(browser, params):
    return {}
"""
    result = NanoScriptAstValidator().validate(code)
    assert result.ok is False


def test_ast_validator_rejects_open_eval_and_while_true() -> None:
    code_open = """
def script(browser, params):
    return open('/etc/passwd').read()
"""
    code_eval = """
def script(browser, params):
    return eval('1+1')
"""
    code_while = """
def script(browser, params):
    while True:
        pass
    return {}
"""

    assert NanoScriptAstValidator().validate(code_open).ok is False
    assert NanoScriptAstValidator().validate(code_eval).ok is False
    assert NanoScriptAstValidator().validate(code_while).ok is False


def test_ast_validator_accepts_valid_script() -> None:
    code = """
def script(browser, params):
    browser.goto(params['url'])
    return {'ok': True}
"""
    result = NanoScriptAstValidator().validate(code)
    assert result.ok is True
