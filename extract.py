#!/usr/bin/env python3
"""A script to download WhatsApp backups from Google Drive."""

import configparser
import hashlib
import json
import os
import sys
import traceback
from base64 import b64decode
from configparser import NoSectionError, NoOptionError
from datetime import datetime
from getpass import getpass
from multiprocessing.pool import ThreadPool
from textwrap import dedent
from typing import Any, Iterator, Optional, TextIO
from urllib import parse

import gpsoauth
import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException


def human_datetime(iso_datetime: str) -> str:
    """Return the nicely human-readable version of a ISO-8601 datetime string."""
    return datetime.fromisoformat(iso_datetime.replace('Z', '')).strftime('%H:%M on %d %b %Y')


def human_size(size: int) -> str:
    """Return the human-readable size of a number of bytes."""
    for s in ['B', 'kiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB']:
        if abs(size) < 1024:
            return f'{size:.2f} {s}'

        size /= 1024

    # If we didn't hit a good size, then just go with the max
    return f'{size:.2f} YiB'


def have_file(file: str, size: int, md5: bytes) -> bool:
    """Determine whether the named file's contents have the given size and hash."""
    if not os.path.exists(file) or size != os.path.getsize(file):
        return False

    digest = hashlib.md5()

    with open(file, 'br') as inp:
        while True:
            b = inp.read(8 * 1024)

            if not b:
                break

            digest.update(b)

    return md5 == digest.digest()


def download_file(file: str, stream: requests.Response) -> None:
    """Download a file from the given stream."""
    os.makedirs(os.path.dirname(file), exist_ok=True)

    with open(file, 'bw') as dest:
        for chunk in stream.iter_content(chunk_size=None):
            dest.write(chunk)


class WaBackup:
    """Provide access to WhatsApp backups stored in Google drive."""

    def __init__(self, gmail: str, password: str, android_id: str):
        """Authorise this script with the given details."""
        token = gpsoauth.perform_master_login(gmail, password, android_id)

        if 'Token' not in token:
            sys.exit(1)

        self.auth = gpsoauth.perform_oauth(
            gmail,
            token['Token'],
            android_id,
            'oauth2:https://www.googleapis.com/auth/drive.appdata',
            'com.whatsapp',
            '38a0f7d505fe18fec64fbf343ecaaaf310dbd799'
        )

    def get(self, path: str, params: Optional[dict[str, str]] = None, **kwargs) -> requests.Response:
        """Perform a get request to the given path in the Google APIs, and return the response object."""
        path = parse.quote(path)

        response = requests.Response()

        try:
            response = requests.get(
                f'https://backup.googleapis.com/v1/{path}',
                headers={'Authorization': f'Bearer {self.auth["Auth"]}'},
                params=params,
                **kwargs
            )
            response.raise_for_status()

        except HTTPError as err_http:
            print('\n\nHTTP Error:', err_http)

        except ConnectionError as err_conn:
            print('\n\nError Connecting:', err_conn)

        except Timeout as err_timeout:
            print('\n\nTimeout Error:', err_timeout)

        except RequestException as err_req:
            print('\n\nOops: Something Else', err_req)

        return response

    def get_page(self, path: str, page_token: Optional[str] = None) -> Any:
        """Return the JSON of a get request to the given path and optional page token."""
        return self.get(path, None if page_token is None else {'pageToken': page_token}).json()

    def list_path(self, path: str) -> Iterator[Any]:
        """Iterate through every item in every page in the given path. This is a generator."""
        last_component = os.path.split(path)[-1]
        page_token = None

        while True:
            page = self.get_page(path, page_token)

            for item in page[last_component]:
                yield item

            if 'nextPageToken' not in page:
                break

            page_token = page['nextPageToken']

    def backups(self) -> Iterator[Any]:
        """Iterate through every backup. This is a generator."""
        return self.list_path('clients/wa/backups')

    def backup_files(self, backup: dict[str, str]) -> Iterator[Any]:
        """Iterate through every file in the given backup. This is a generator."""
        return self.list_path(f'{backup["name"]}/files')

    def fetch(self, file: dict[str, str]) -> tuple[str, int, bytes]:
        """Download the file, and then return the name, size, and hash of the given file dictionary."""
        name = os.path.join(*file['name'].split("/")[3:])
        md5_hash = b64decode(file['md5Hash'], validate=True)

        if not have_file(name, int(file['sizeBytes']), md5_hash):
            download_file(name, self.get(file['name'], {'alt': 'media'}, stream=True))

        return name, int(file['sizeBytes']), md5_hash

    def fetch_all(self, backup: dict[str, str], cksums: TextIO) -> None:
        """Fetch every file in the backup, and write their checksums to the cksums file object."""
        num_files = 0
        total_size = 0

        with ThreadPool(10) as pool:
            downloads = pool.imap_unordered(self.fetch, self.backup_files(backup))

            for name, size, md5_hash in downloads:
                num_files += 1
                total_size += size

                # The \r here is to move the cursor to the start of the line every time
                print(
                    f'\rProgress: {100 * total_size / int(backup["sizeBytes"]):7.3f}% '
                    f'{os.path.basename(name)[-60:]:60}', end='', flush=True
                )

                cksums.write(f'{md5_hash.hex()} *{name}\n')

        print(f'\n\n{num_files} files ({human_size(total_size)})')


def get_configs() -> dict[str, str]:
    """Read from the config file and return a dictionary of its values."""
    config = configparser.ConfigParser()

    try:
        config.read('settings.cfg')

        android_id = config.get('auth', 'android_id')
        gmail = config.get('auth', 'gmail')
        password = config.get('auth', 'password', fallback='')

        if not password:
            try:
                password = getpass(f'Enter your password for {gmail}: ')

            except KeyboardInterrupt:
                print("\nCancelled!")
                sys.exit(1)

        return {
            'android_id': android_id,
            'gmail': gmail,
            'password': password
        }

    except (NoSectionError, NoOptionError):
        print('The "settings.cfg" file is missing or corrupt!')
        sys.exit(1)


def create_settings_file() -> None:
    """Write the default contents to a new settings.cfg file."""
    with open('settings.cfg', 'w', encoding='utf-8') as cfg:
        cfg.write(dedent('''
            [auth]
            gmail = alias@gmail.com
            # Optional. The account password or app password when using 2FA.
            # You will be prompted if omitted.
            password = yourpassword
            # The result of "adb shell settings get secure android_id".
            android_id = 0000000000000000
            '''))


def backup_info(backup: dict[str, str]) -> None:
    """Print the info of the given backup."""
    metadata = json.loads(backup['metadata'])

    for size in 'backupSize', 'chatdbSize', 'mediaSize', 'videoSize':
        metadata[size] = human_size(int(metadata[size]))

    print(f'\nBackup: {os.path.split(backup["name"])[-1]}')
    print(f'Size: ({metadata["backupSize"]}) Upload Time: {human_datetime(backup["updateTime"])}\n')

    print(f'  WhatsApp version  : {metadata["versionOfAppWhenBackup"]}')

    try:
        print(f'  Password protected: {metadata["passwordProtectedBackupEnabled"]}')
    except KeyError:
        pass

    print(f'  Messages          : {metadata["numOfMessages"]} ({metadata["chatdbSize"]})')
    print(f'  Media files       : {metadata["numOfMediaFiles"]} ({metadata["mediaSize"]})')
    print(f'  Photos            : {metadata["numOfPhotos"]}')
    print(f'  Videos            : included={metadata["includeVideosInBackup"]} ({metadata["videoSize"]})')
    print()


def main(args: list[str]) -> None:
    """Run the desired action, as specified by args. The action can be any of info, list, or sync."""
    if len(args) != 2 or args[1] not in ('info', 'list', 'sync'):
        print(f'\nusage: python3 {args[0]} help|info|list|sync\n')
        print('info    Show WhatsApp backups.')
        print('list    Show WhatsApp backup files.')
        print('sync    Download all WhatsApp backups.\n')

        sys.exit(0)

    if not os.path.isfile('settings.cfg'):
        create_settings_file()

    wa_backup = WaBackup(**get_configs())
    backups = wa_backup.backups()

    if args[1] == 'info':
        for backup in backups:
            answer = input(f'\nDo you want {os.path.split(backup["name"])[-1]}? (y/N): ')

            if not answer or answer[0].lower() != 'y':
                continue

            backup_info(backup)

    elif args[1] == 'list':
        for backup in backups:
            answer = input(f'\nDo you want {os.path.split(backup["name"])[-1]}? (y/N): ')

            if not answer or answer[0].lower() != 'y':
                continue

            print()
            num_files = 0
            total_size = 0

            for file in wa_backup.backup_files(backup):
                try:
                    num_files += 1
                    total_size += int(file['sizeBytes'])
                    print(os.path.join(*file['name'].split("/")[3:]))

                except (KeyError, ValueError):
                    print(dedent(f'''
                    #####

                    Warning: Unexpected error in file "{os.path.join(file["name"].split("/")[3:])}"

                    Detail: {json.dumps(file, indent=4, sort_keys=True)}

                    Exception: {traceback.format_exc()}

                    #####
                    '''))
                    input('Press the <Enter> key to continue...')
                    continue

            print(f'\n{num_files} files ({human_size(total_size)})\n')

    elif args[1] == 'sync':
        with open('md5sum.txt', 'w', encoding='utf-8', buffering=1) as cksums:
            for backup in backups:
                try:
                    answer = input(f'\nDo you want {os.path.split(backup["name"])[-1]}? (y/N): ')

                    if not answer or answer[0].lower() != 'y':
                        continue

                    print(f'\nBackup Size: {human_size(int(backup["sizeBytes"]))} '
                          f'Upload Time: {human_datetime(backup["updateTime"])}\n')

                    wa_backup.fetch_all(backup, cksums)

                    print()

                except (KeyError, ValueError):
                    print(dedent(f'''
                    #####

                    Warning: Unexpected error in backup: {backup["name"].split("/")[-1]}
                    (Size: {human_size(int(backup["sizeBytes"]))} Upload Time: {human_datetime(backup["updateTime"])})

                    Exception: {traceback.format_exc()}

                    #####
                    '''))
                    input("Press the <Enter> key to continue...")


if __name__ == '__main__':
    main(sys.argv)
