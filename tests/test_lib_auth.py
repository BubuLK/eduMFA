"""
This tests the files
  lib/auth.py and
"""

from .base import MyTestCase
from edumfa.lib.auth import (create_db_admin, verify_db_admin,
                             delete_db_admin,
                             check_webui_user, db_admin_exist)
from edumfa.lib.user import User
from flask import current_app


class AuthTestCase(MyTestCase):
    """
    Test the Auth module
    """

    def test_01_db_admin(self):
        create_db_admin("mytestadmin", email="admin@localhost", password="PSTwort")
        r = verify_db_admin("mytestadmin", "PSTwort")
        self.assertTrue(r)

        self.assertTrue(db_admin_exist("mytestadmin"))
        self.assertFalse(db_admin_exist("noKnownUser"))

        # Delete the admin
        delete_db_admin("mytestadmin")

    def test_02_users(self):
        r, role, detail = check_webui_user(User("cornelius"), "test")
        self.assertFalse(r)
        self.assertEqual(role, "user")

    def test_03_empty_passsword(self):
        create_db_admin("mytestadmin", email="admin@localhost", password="PSTwort")
        r = verify_db_admin("mytestadmin", None)
        self.assertFalse(r)

        # Delete the admin
        delete_db_admin("mytestadmin")
