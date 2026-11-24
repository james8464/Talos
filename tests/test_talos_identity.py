"""Identity resolution reads the Me card from Contacts source databases."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talos_core.cognition import talos_identity


def make_contacts(root: Path, *, named=True, me=True, address_column=False):
    """Create a minimal Contacts database shaped like macOS 15/26."""
    source = root / "Library" / "Application Support" / "AddressBook" / "Sources" / "AAAA"
    source.mkdir(parents=True)
    database = source / "AddressBook-v22.abcddb"
    with sqlite3.connect(database) as db:
        address = ", ZADDRESS TEXT" if address_column else ""
        db.execute(
            "CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZME INTEGER, "
            f"ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT{address})"
        )
        db.execute(
            "CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT)"
        )
        db.execute(
            "CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT)"
        )
        db.execute(
            "INSERT INTO ZABCDRECORD (Z_PK) VALUES (1)"
        )
        db.execute(
            "INSERT INTO ZABCDRECORD (Z_PK, ZME, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION) "
            "VALUES (15, ?, ?, ?, ?)",
            (1 if me else None, "Casey" if named else None,
             "Example" if named else None, None),
        )
        if named:
            db.execute(
                "INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS) VALUES (15, ?)",
                ("casey@example.invalid",),
            )
            db.execute(
                "INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER) VALUES (15, ?)",
                ("+10000000000",),
            )


class IdentityTests(unittest.TestCase):
    def resolve(self, root: Path):
        with patch.object(talos_identity.Path, "home", return_value=root):
            return talos_identity.IdentityResolver(Path("unused")).get_unified_profile()

    def test_me_card_in_source_database_resolves_without_address_column(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_contacts(root)
            profile = self.resolve(root)
        self.assertEqual(profile["full_name"], "Casey Example")
        self.assertEqual(profile["emails"], ["casey@example.invalid"])
        self.assertEqual(profile["phones"], ["+10000000000"])
        self.assertIsNone(profile["address"])

    def test_no_me_card_never_uses_an_arbitrary_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_contacts(root, me=False)
            profile = self.resolve(root)
        self.assertIsNone(profile["full_name"])

    def test_unnamed_me_card_does_not_claim_a_placeholder_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_contacts(root, named=False)
            profile = self.resolve(root)
        self.assertFalse(profile["full_name"])


if __name__ == "__main__":
    unittest.main()
