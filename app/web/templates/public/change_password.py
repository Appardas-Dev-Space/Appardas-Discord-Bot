"""Mandatory password change view for preliminary accounts."""

from __future__ import annotations

from ..base import csrf_field, e


def change_password_page(*, csrf: str, message: str = "", next_path: str = "/dashboard") -> str:
    notice = f'<p class="login-error" role="status">{e(message)}</p>' if message else ''
    return f'''<div class="login-main">
  <h1>Change password.</h1>
  <p>You are using a preliminary or temporary password. Please set a new secure password to unlock console access.</p>
  {notice}
  <form method="post" action="/change-password" class="login-form">
    {csrf_field(csrf)}
    <input type="hidden" name="next" value="{e(next_path)}">
    <div class="form-field">
      <label for="current_password">Current Password</label>
      <input type="password" id="current_password" name="current_password" required autocomplete="current-password" autofocus>
    </div>
    <div class="form-field">
      <label for="new_password">New Password</label>
      <input type="password" id="new_password" name="new_password" minlength="6" required autocomplete="new-password">
    </div>
    <div class="form-field">
      <label for="confirm_password">Confirm New Password</label>
      <input type="password" id="confirm_password" name="confirm_password" minlength="6" required autocomplete="new-password">
    </div>
    <button type="submit" class="primary">Update Password & Proceed</button>
  </form>
  <p class="login-detail">Password must be at least 6 characters and different from the default password. Once updated, your console session will be fully activated.</p>
</div>'''
