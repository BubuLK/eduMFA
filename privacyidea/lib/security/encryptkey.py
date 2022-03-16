# -*- coding: utf-8 -*-
#
#  2022-03-10 Cornelius Kölbel <cornelius.koelbel@netknights.it>
#             Init

#
# This code is free software; you can redistribute it and/or
# modify it under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE
# License as published by the Free Software Foundation; either
# version 3 of the License, or any later version.
#
# This code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU AFFERO GENERAL PUBLIC LICENSE for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
import contextlib
import getopt
import logging
from privacyidea.lib.security.default import DefaultSecurityModule
from privacyidea.lib.error import HSMException
from six import int2byte
from getpass import getpass
import sys


__doc__ = """
This is a PKCS11 Security module that decrypts a given encrypted key file
in the HSM with an asymmetric private key.

Please see the docstring of the SecurityModule below, on how to configure this
module in pi.cfg.

  python encryptkey.py --module /usr/lib/libykcs11.so --keyid 1 --slotname "Yubico YubiKey" \
                       --infile enckey --outfile enckey.enc

  python encryptkey.py --module /usr/lib/libykcs11.so --keylabel "my secret key" --slotname "Yubico YubiKey" \
                       --infile enckey --outfile enckey.enc
"""

log = logging.getLogger(__name__)

try:
    import PyKCS11
    MECHANISM = PyKCS11.CKM_RSA_PKCS
except ImportError:
    log.info("The python module PyKCS11 is not available. "
             "So we can not use the PKCS11 security module.")


def int_list_to_bytestring(int_list):  # pragma: no cover
    return b"".join([int2byte(i) for i in int_list])

import os
import time
DIR = "/dev/shm/pilock"

@contextlib.contextmanager
def hsm_lock(timeout=15):
    # Wait, that we are free to go
    while timeout > 0:
        timeout -= 1
        if os.path.exists(DIR):
            time.sleep(1)
        else:
            break
    # Lock, since we are acting.
    os.mkdir(DIR)
    yield
    # Cleanup
    os.rmdir(DIR)


class EncryptKeyHardwareSecurityModule(DefaultSecurityModule):  # pragma: no cover

    def __init__(self, config=None, logout=True):
        """
        Initialize the PKCS11 Security Module.
        The configuration needs to contain the pkcs11 module and the ID of the key.

        {"module": "/usr/lib/libykcs11.so",
         "slotname": "Yubico YubiKey",
         "keyid": 1,
         "keylabel": "my secret key"
         "encfile": "/etc/privacyidea/enckey.enc",
         "password", "123456"}

        The encfile is the encrypted encryption key.

        The values can be configured in the pi.cfg file like this:

        PI_HSM_MODULE = "privacyidea.lib.security.encryptkey.EncryptKeyHardwareSecurityModule"
        PI_HSM_MODULE_MODULE = "/usr/lib/libykcs11.so"
        PI_HSM_MODULE_SLOTNAME = "Yubico YubiKey"
        # Alternative to slotname:
        # PI_HSM_MODULE_SLOT = 1
        PI_HSM_MODULE_KEYID = 1
        # Alternative to KEYID -- only use one of both!
        PI_HSM_MODULE_KEYLABEL = "my secret key"
        PI_HSM_MODULE_PASSWORD = '123456'
        PI_HSM_MODULE_ENCFILE = "/etc/privacyidea/enckey.enc"

        :param config: contains the HSM configuration
        :type config: dict

        :return: The Security Module object
        """
        self.config = config or {}
        self.secrets = {}
        self.name = "HSM"
        self.slot = self.config.get("slot") or -1
        self.slotname = self.config.get("slotname")
        if "module" not in config:
            log.error("No PKCS11 module defined!")
            raise HSMException("No PKCS11 module defined.")
        else:
            self.module = self.config.get("module")

        if "keyid" not in config and "keylabel" not in config:
            log.error("No keyid or keylabel defined.")
            raise HSMException("No keyid or keylabel defined.")
        self.keyid = self.config.get("keyid")
        self.keylabel = self.config.get("keylabel")

        if "password" in self.config:
            self.password = self.config.get("password")
        else:
            log.error("No password specified.")
            self.password = getpass()
        # Now we have our password
        self.is_ready = False

        with hsm_lock():
            self.pkcs11 = PyKCS11.PyKCS11Lib()
            self.pkcs11.load(self.config.get("module"))
            self.pkcs11.lib.C_Initialize()

            slotlist = self.pkcs11.getSlotList()
            log.debug("Found the slots: {0!s}".format(slotlist))
            if not len(slotlist):
                raise HSMException("No HSM connected. No slots found.")

            if self.slot == -1:
                if len(slotlist) == 1:
                    # Use the first and only slot
                    self.slot = slotlist[0]
                elif len(slotlist) > 1:
                    for slot in slotlist:
                        # Find the slot via the slotname
                        slotinfo = self.pkcs11.getSlotInfo(slot)
                        log.debug("Found slot '{}'".format(slotinfo.slotDescription))
                        if slotinfo.slotDescription.startswith(self.slotname):
                            self.slot = slot
                            break

            if self.slot not in slotlist:
                raise HSMException("Slot {0:d} not present".format(self.slot))

            slotinfo = self.pkcs11.getSlotInfo(self.slot)
            log.debug("Setting up slot {0!s}: '{1!s}'".format(self.slot, slotinfo.slotDescription))

            self.session = self.pkcs11.openSession(slot=self.slot)
            log.debug("Logging on to '{}'".format(slotinfo.slotDescription))
            try:
                self.session.login(self.password)
            except PyKCS11.PyKCS11Error as e:
                if str(e).startswith("CKR_USER_ALREADY_LOGGED_IN"):
                    log.info("Timing issues. We need to relogin the user.")
                    # in case the user is already logged in
                    self.session.logout()
                    self.session.login(self.password)
                elif str(e).startswith("CKR_PIN_INCORRECT"):
                    log.error("A wrong HSM Password is configured. Please check your configuration in pi.cfg")
                    # We reset the password, to avoid future PIN Locking!
                    # I think this does not work between processes!
                    self.password = None
                    raise e
                else:
                    raise e

            if "encfile" in self.config:
                self._decrypt_file(self.config.get("encfile"))
            log.debug("Successfully setup the security module.")
            self.is_ready = True
            # We need this for the base class
            self.crypted = True
            if logout:
                self.session.logout()
                self.session.closeSession()

    def _add_template(self, template):
        if self.keyid:
            template.append((PyKCS11.CKA_ID, (self.keyid,)))
        elif self.keylabel:
            template.append((PyKCS11.CKA_LABEL, self.keylabel))
        return template

    def _get_private_key(self):
        """
        Returns the handle to the private key for decryption.
        """
        objs = self.session.findObjects(self._add_template([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY)]))
        log.debug("Found {0!s} private keys.".format(len(objs)))
        for obj in objs:
            log.debug("========================================================")
            log.debug("Found object {0!s}".format(obj))
        return objs[0]

    def _encrypt_file(self, infile, outfile):
        """
        This is only used to create the encrypted file, that holds the encryption key.
        :param infile: The encryption key in plain text
        :param outfile: The encrypted encryption key
        """
        with open(infile, "rb") as f:
            enckey = f.read()
        objs = self.session.findObjects(self._add_template([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PUBLIC_KEY)]))
        log.debug("Found {0!s} public keys.".format(len(objs)))
        for obj in objs:
            log.debug("========================================================")
            log.debug("Found object {0!s}".format(obj))
        pubkey = objs[0]
        m = PyKCS11.Mechanism(MECHANISM)
        r = self.session.encrypt(pubkey, enckey, m)
        with open(outfile, "wb") as f:
            f.write(bytearray(r))

    def _listkeys(self, keytype="public"):
        if keytype == "public":
            objs = self.session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PUBLIC_KEY)])
        else:
            objs = self.session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY)])
        log.debug("Found {0!s} keys.".format(len(objs)))
        for obj in objs:
            log.debug("========================================================")
            log.debug("Found object {0!s}".format(obj))

    def _decrypt_file(self, filename):
        f = open(filename, "rb")
        filecontents = f.read()
        f.close()
        privkey = self._get_private_key()
        m = PyKCS11.Mechanism(MECHANISM)
        r = self.session.decrypt(privkey, filecontents, m)
        r = int_list_to_bytestring(r)
        for key_id in [0, 1, 2]:
            self.secrets[key_id] = r[key_id * 32: (key_id + 1) * 32]
        log.info("Successfully loaded encryption keys into process.")

    def _get_secret(self, slot_id=0, password=None):
        """
        This replaces the _get_secret method from the base class to return
        one of the three encryption keys.

        :param slot_id: The id of the encryption key
        :param password: n/a
        :return:
        """
        key = self.secrets.get(slot_id)
        return key

    def setup_module(self, params):
        """

        :param params:
        :return:
        """
        log.warning("The method 'setupmodule' is not implemented and can not be used.")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig()
    log.setLevel(logging.INFO)
    log.setLevel(logging.DEBUG)

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hm:s:k:i:o:e:l",
                                   ["help", "module=", "slotname=", "keyid=",
                                    "infile=", "outfile=",
                                    "encfile=", "listprivate", "listpublic", "keylabel="])
    except getopt.GetoptError as e:
        print(str(e))
        sys.exit(1)

    infile = outfile = None
    config = {}
    listkeys = False

    for o, a in opts:
        if o in ("-m", "--module"):
            config["module"] = a
        elif o in ("-s", "--slotname"):
            config["slotname"] = a
        elif o in ("-k", "--keyid"):
            config["keyid"] = int(a)
        elif o in ("--keylabel"):
            config["keylabel"] = a
        elif o in ("-i", "--infile"):
            infile = a
        elif o in ("-o", "--outfile"):
            outfile = a
        elif o in ("-e", "--encfile"):
            config["encfile"] = a
        elif o in ("-l", "--listpublic"):
            listkeys = "public"
        elif o in ("-l", "--listprivate"):
            listkeys = "private"

    p = EncryptKeyHardwareSecurityModule(config, logout=False)

    if infile and outfile:
        p._encrypt_file(infile, outfile)

    if listkeys:
        p._listkeys(listkeys)

    if "encfile" in config:
        # password
        password = "topSekr3t" * 16
        crypted = p.encrypt_password(password)
        text = p.decrypt_password(crypted)
        assert(text == password)
        log.info("password encrypt/decrypt test successful")

        # pin
        password = "topSekr3t"
        crypted = p.encrypt_pin(password)
        text = p.decrypt_pin(crypted)
        assert (text == password)
        log.info("pin encrypt/decrypt test successful")

        # random
        tmp_iv = p.random(16)
        plain = p.random(128)
        log.info("random test successful")

        # generic encrypt / decrypt
        cipher = p.encrypt(plain, tmp_iv)
        assert (plain != cipher)
        text = p.decrypt(cipher, tmp_iv)
        assert (text == plain)
        log.info("generic encrypt/decrypt test successful")