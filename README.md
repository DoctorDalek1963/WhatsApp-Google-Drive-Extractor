# WhatsApp Google Drive Extractor

Allows WhatsApp users on Android to extract their backed up WhatsApp data
from Google Drive.

## Prerequisites

 1. [Python 3](https://www.python.org/downloads/)
 2. Android device with WhatsApp installed and the Google Drive backup
    feature enabled.
 3. The device's Android ID. This can be obtained via any of the various apps 
    on the Play Store, or by running `adb shell settings get secure android_id`
    if you've got developer settings and `adb`.
 4. Google account login credentials (gmail and password). A separate app password
    will be needed if you've got 2FA enabled. This can be set up [here](https://myaccount.google.com/apppasswords)
    and will need to be entered as your password in `settings.cfg`.

## Instructions

 1. Clone this repo, or download the zip file and extract all of it.
 2. Install dependencies: Run `python3 -m pip install -r requirements.txt`
    from your command console. A virtual environment is recommended if you
    know how to do that, but it's not necessary.
 3. Edit the `[auth]` section in `settings.cfg`.
 4. Run `python3 extract.py` from your command console.
 5. Read the usage examples and run one of them.

If downloading is interrupted, the files that were received successfully
won't be re-downloaded when running the tool again. After downloading,
you may verify the integrity of the downloaded files using `md5sum
--check md5sum.txt` on MacOS or Linux or [md5summer](http://md5summer.org/) on Windows.

## Troubleshooting

 1. Check that you have the required dependencies installed: `python3 -m pip
    install -r requirements`
 2. If you have `Error:Need Browser`, go to this url to solve the issue:
    https://accounts.google.com/b/0/DisplayUnlockCaptcha

## Credits

Author: TripCode

Contributors: DrDeath1122 from XDA for the multi-threading backbone part,
YuriCosta for reverse engineering the new restore system,
DoctorDalek1963 for making the code more Pythonic and making the output easier to read
