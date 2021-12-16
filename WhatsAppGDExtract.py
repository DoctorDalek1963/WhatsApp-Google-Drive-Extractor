#!/usr/bin/env python3
"""A script to download WhatsApp backups from Google Drive."""

import configparser
import gpsoauth
import hashlib
import json
import os
import requests
import sys
import traceback
from base64 import b64decode
from configparser import NoSectionError, NoOptionError
from getpass import getpass
from multiprocessing.pool import ThreadPool
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException
from textwrap import dedent


def human_size(size):
    """Return the human-readable size of a number of bytes."""
    for s in ['B', 'kiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']:
        if abs(size) < 1024:
            return f'{size:.2f}{s}'

        size = int(size / 1024)


def have_file(file, size, md5):
    """Determine whether the named file's contents have the given size and hash."""
    if not os.path.exists(file) or size != os.path.getsize(file):
        return False

    digest = hashlib.md5()

    with open(file, "br") as inp:
        while True:
            b = inp.read(8 * 1024)

            if not b:
                break

            digest.update(b)

    return md5 == digest.digest()


def download_file(file, stream):
    """Download a file from the given stream."""
    os.makedirs(os.path.dirname(file), exist_ok=True)

    with open(file, "bw") as dest:
        for chunk in stream.iter_content(chunk_size=None):
            dest.write(chunk)


class WaBackup:
    """Provide access to WhatsApp backups stored in Google drive."""
    def __init__(self, gmail, password, android_id):
        token = gpsoauth.perform_master_login(gmail, password, android_id)

        if "Token" not in token:
            quit(token)

        self.auth = gpsoauth.perform_oauth(
            gmail,
            token["Token"],
            android_id,
            "oauth2:https://www.googleapis.com/auth/drive.appdata",
            "com.whatsapp",
            "38a0f7d505fe18fec64fbf343ecaaaf310dbd799"
        )

    def get(self, path, params=None, **kwargs):
        """Perform a get request to the given path in the Google APIs, and return the response object."""
        try:
            response = requests.get(
                "https://backup.googleapis.com/v1/{}".format(path),
                headers={"Authorization": "Bearer {}".format(self.auth["Auth"])},
                params=params,
                **kwargs
            )

            response.raise_for_status()
        except HTTPError as err_http:
            print("\n\nHttp Error:", err_http)

        except ConnectionError as err_conn:
            print("\n\nError Connecting:", err_conn)

        except Timeout as err_timeout:
            print("\n\nTimeout Error:", err_timeout)

        except RequestException as err_req:
            print("\n\nOops: Something Else", err_req)

        return response

    def get_page(self, path, page_token=None):
        """Return the JSON of a get request to the given path and optional page token."""
        return self.get(
            path,
            None if page_token is None else {"pageToken": page_token}
        ).json()

    def list_path(self, path):
        """Iterate through every item in every page in the given path. This is a generator."""
        last_component = path.split("/")[-1]
        page_token = None

        while True:
            page = self.get_page(path, page_token)

            for item in page[last_component]:
                yield item

            if "nextPageToken" not in page:
                break

            page_token = page["nextPageToken"]

    def backups(self):
        """Iterate through every backup. This is a generator."""
        return self.list_path("clients/wa/backups")

    def backup_files(self, backup):
        """Iterate through every file in the given backup. This is a generator."""
        return self.list_path("{}/files".format(backup["name"]))

    def fetch(self, file):
        """Download the file, and then return the name, size, and hash of the given file dictionary."""
        name = os.path.sep.join(file["name"].split("/")[3:])
        md5Hash = b64decode(file["md5Hash"], validate=True)

        if not have_file(name, int(file["sizeBytes"]), md5Hash):
            download_file(
                name,
                self.get(file["name"].replace("%", "%25").replace("+", "%2B"), {"alt": "media"}, stream=True)
            )

        return name, int(file["sizeBytes"]), md5Hash

    def fetch_all(self, backup, cksums):
        """Fetch every file in the backup, and write their checksums to the cksums file object."""
        num_files = 0
        total_size = 0

        with ThreadPool(10) as pool:
            downloads = pool.imap_unordered(
                lambda file: self.fetch(file),
                self.backup_files(backup)
            )

            for name, size, md5Hash in downloads:
                num_files += 1
                total_size += size

                print(
                    "\rProgress: {:7.3f}% {:60}".format(
                        100 * total_size / int(backup["sizeBytes"]),
                        os.path.basename(name)[-60:]
                    ),
                    end="",
                    flush=True
                )

                cksums.write("{md5Hash} *{name}\n".format(
                    name=name,
                    md5Hash=md5Hash.hex()
                ))

        print("\n{} files ({})".format(num_files, human_size(total_size)))


def get_configs():
    """Read from the config file and return a dictionary of its values."""
    config = configparser.ConfigParser()

    try:
        config.read("settings.cfg")

        android_id = config.get("auth", "android_id")
        gmail = config.get("auth", "gmail")
        password = config.get("auth", "password", fallback="")

        if not password:
            try:
                password = getpass("Enter your password for {}: ".format(gmail))

            except KeyboardInterrupt:
                quit("\nCancelled!")

        return {
            "android_id": android_id,
            "gmail": gmail,
            "password": password
        }

    except (NoSectionError, NoOptionError):
        quit("The 'settings.cfg' file is missing or corrupt!")


def create_settings_file():
    """Write the default contents to a new settings.cfg file."""
    with open("settings.cfg", "w") as cfg:
        cfg.write(dedent("""
            [auth]
            gmail = alias@gmail.com
            # Optional. The account password or app password when using 2FA.
            # You will be prompted if omitted.
            password = yourpassword
            # The result of "adb shell settings get secure android_id".
            android_id = 0000000000000000
            """).lstrip())


def backup_info(backup):
    metadata = json.loads(backup["metadata"])

    for size in "backupSize", "chatdbSize", "mediaSize", "videoSize":
        metadata[size] = human_size(int(metadata[size]))

    print("Backup {} Size:({}) Upload Time:{}".format(backup["name"].split("/")[-1], metadata["backupSize"], backup["updateTime"]))
    print("  WhatsApp version  : {}".format(metadata["versionOfAppWhenBackup"]))

    try:
        print("  Password protected: {}".format(metadata["passwordProtectedBackupEnabled"]))

    except:
        pass

    print("  Messages          : {} ({})".format(metadata["numOfMessages"], metadata["chatdbSize"]))
    print("  Media files       : {} ({})".format(metadata["numOfMediaFiles"], metadata["mediaSize"]))
    print("  Photos            : {}".format(metadata["numOfPhotos"]))
    print("  Videos            : included={} ({})".format(metadata["includeVideosInBackup"], metadata["videoSize"]))


def main(args):
    """Run the desired action, as specified by args. The action can be any of info, list, or sync."""
    if len(args) != 2 or args[1] not in ("info", "list", "sync"):
        print(f'\nusage: python3 {args[0]} help|info|list|sync\n')
        print('info    Show WhatsApp backups.')
        print('list    Show WhatsApp backup files.')
        print('sync    Download all WhatsApp backups.\n')

        sys.exit(0)

    if not os.path.isfile("settings.cfg"):
        create_settings_file()

    wa_backup = WaBackup(**get_configs())
    backups = wa_backup.backups()

    if args[1] == "info":
        for backup in backups:
            answer = input("\nDo you want {}? [y/n] : ".format(backup["name"].split("/")[-1]))

            if not answer or answer[0].lower() != 'y':
                continue

            backup_info(backup)

    elif args[1] == "list":
        for backup in backups:
            answer = input("\nDo you want {}? [y/n] : ".format(backup["name"].split("/")[-1]))

            if not answer or answer[0].lower() != 'y':
                continue

            num_files = 0
            total_size = 0

            for file in wa_backup.backup_files(backup):
                try:
                    num_files += 1
                    total_size += int(file["sizeBytes"])
                    print(os.path.sep.join(file["name"].split("/")[3:]))

                except:
                    print("\n#####\n\nWarning: Unexpected error in file: {}\n\nDetail: {}\n\nException: {}\n\n#####\n".format(
                        os.path.sep.join(file["name"].split("/")[3:]),
                        json.dumps(file, indent=4, sort_keys=True),
                        traceback.format_exc()
                    ))
                    input("Press the <Enter> key to continue...")
                    continue

            print("{} files ({})".format(num_files, human_size(total_size)))

    elif args[1] == "sync":
        with open("md5sum.txt", "w", encoding="utf-8", buffering=1) as cksums:
            for backup in backups:
                try:
                    answer = input("\nDo you want {}? [y/n] : ".format(backup["name"].split("/")[-1]))

                    if not answer or answer[0].lower() != 'y':
                        continue

                    print("Backup Size:{} Upload Time: {}".format(human_size(int(backup["sizeBytes"])), backup["updateTime"]))

                    wa_backup.fetch_all(backup, cksums)
                except Exception as err:
                    print("\n#####\n\nWarning: Unexpected error in backup: {} (Size:{} Upload Time: {})\n\nException: {}\n\n#####\n".format(
                        backup["name"].split("/")[-1],
                        human_size(int(backup["sizeBytes"])),
                        backup["updateTime"],
                        traceback.format_exc()
                    ))
                    input("Press the <Enter> key to continue...")


if __name__ == "__main__":
    main(sys.argv)
