"""Operator credential sign-in and security details."""

from __future__ import annotations

from ..base import e


def login_page(*, next_path: str = "/dashboard", message: str = "") -> str:
    notice = f'<p class="login-error" role="status">{e(message)}</p>' if message else ''
    form = f'''<form method="post" action="/login" class="login-form">
  <input type="hidden" name="next" value="{e(next_path)}">
  <div class="form-field">
    <label for="username">Username</label>
    <input type="text" id="username" name="username" required autocomplete="username" autofocus>
  </div>
  <div class="form-field">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required autocomplete="current-password">
  </div>
  <button type="submit" class="primary">Sign In</button>
</form>'''
    return f'''<div class="login-main">
  <h1>Sign in.</h1>
  <p>Accounts are created by the bot owner. Sign in with the username and password they provide, not your Discord ID. Temporary passwords must be changed before accessing the console.</p>
  {notice}
  {form}
  <p class="login-detail">A secure session cookie keeps you signed in for up to 30 idle days (90 days total). Sign-ins and console operations are recorded in the audit activity log with your account, IP, and timestamp. No analytics or external tracking.</p>
</div>'''
