# konica-back

A digital back for a **Konica Auto S3**: an IMX477 sensor (Innomaker HQ module)
sits at the film plane, run by a **Raspberry Pi Zero 2 W** on a 3.7 V 300 mAh
LiPo. It saves **DNG raw**, **JPEG**, and/or **HEIF**.

## How you take a picture

**Lens mode** (default): the Konica's own leaf shutter and auto-exposure set
the exposure.

1. Press the **shutter button on the back**. The LED comes on **solid green**.
2. While it's lit (up to ~3 s), fire the **camera's shutter release**.
3. The LED goes out once the photo is caught, then **blinks green twice**
   when it's saved. One **long red blink** means no light arrived in time.
   A single blue blink at start-up means it's ready.

While idle, the sensor runs back-to-back 1-second exposures, which stay black
while the lens shutter is closed. Pressing the back's button "arms" it. The
frame that catches the light from the lens is saved. You don't need to time
the two presses: if the lens fires across the boundary between two frames,
both raw frames are added together, so the photo comes out whole.

Set `require_button = false` to skip step 1; then any burst of light is saved.

**Sensor mode**: if the lens shutter is held open, the back's button takes a
normal auto-exposed digital photo.

**Shutting down:** the back has no working power button wired. Shut the Pi
down over SSH with `sudo shutdown now` before switching off the battery.
(If you wire a power button later, set `power_gpio` in `config.toml`; holding
it for 2 s then shuts down cleanly.)

**Wiring (BCM GPIO):** shutter button on GPIO17 (header pin 11) to ground;
RGB LED on GPIO27 (pin 13, red), GPIO18 (pin 12, green) and GPIO22 (pin 15,
blue), common ground, each colour through its own resistor. If you rewire,
the `led` test below re-maps the colours.

## First-time setup

1. Use Raspberry Pi Imager to flash **Raspberry Pi OS Lite (64-bit)**. In its
   settings, set hostname `konica`, enable SSH, and add your Wi-Fi.
2. SSH in (`ssh pi@konica.local`) and clone the repo onto the Pi:
   ```bash
   sudo apt-get install -y git
   git clone https://github.com/briwil/Konica.git ~/konica-back
   ~/konica-back/deploy/install.sh
   ```
   This installs packages and sets up two services: `konica-back` (the camera)
   and `konica-update` (updates from GitHub at boot). If the repo is private,
   the Pi first needs a read-only deploy key (see *Updates* below).
3. Check the hardware:
   ```bash
   cd konica-back
   .venv/bin/python -m konicaback info      # camera found? enough CMA memory?
   .venv/bin/python -m konicaback buttons   # press each button and note its GPIO
   .venv/bin/python -m konicaback led       # lights each LED colour in turn
   ```
   The shutter default matches the wiring above. The LED's red/green/blue
   pin numbers are a guess, so the `led` test lights one pin at a time, waits,
   and asks which colour you see (r, g, b, or n for nothing visible). It then saves your
   answers to `config.toml`. The program refuses to start if one pin is used
   twice, and the `led` test refuses to drive a button pin.
4. `sudo systemctl start konica-back`

## Updates

Each time the Pi boots and can reach GitHub, it pulls the latest `main` and
restarts the camera software. Without Wi-Fi it skips this and the camera
starts as usual. It only fast-forwards: if you've edited code directly on the
Pi, it leaves your edits alone and doesn't update. To check what happened:
`journalctl -u konica-update -b`. To update right away without rebooting,
run `~/konica-back/deploy/auto-update.sh`.

**Private repo:** on the Pi, run `ssh-keygen -t ed25519` (press Enter at
every prompt), then `cat ~/.ssh/id_ed25519.pub`. Add that line on GitHub
under *Konica → Settings → Deploy keys* (read-only). Then point the clone at
SSH: `git -C ~/konica-back remote set-url origin git@github.com:briwil/Konica.git`.

## Day to day

| On your computer | What it does |
|---|---|
| `scripts/pull-photos.sh` | copy photos from the Pi into `./photos` |
| `./deploy.sh` | push local, uncommitted code to the Pi for quick testing |

On the Pi, `python -m konicaback shoot` takes one photo from the terminal,
which is handy for testing. Settings live in `~/konica-back/config.toml` on the
Pi (see `config.example.toml`); `deploy.sh` never overwrites that file.

## Calibrating lens mode

- **Exposure:** set the Konica's ASA dial, then set `gain` to about ASA ÷ 100
  (e.g. ASA 200 → gain 2.0). Adjust `gain` from there based on test shots.
- **White balance:** auto white balance can't run on frames that are mostly
  black, so lens mode uses fixed gains. Take a daylight shot in sensor mode
  (lens shutter open). The log prints `ColourGains (r, b)`; copy those numbers
  into `wb_gains`. DNGs are unaffected, since you set white balance when you
  develop them.
- **Orientation:** use `hflip` / `vflip`.

## The pink cast

On HQ-style IMX477 boards, the **IR-cut filter is glued into the lens-mount
ring**. With the ring removed so the sensor can sit at the film plane,
infrared reaches the sensor and everything turns pink/magenta. Fixes:

- **Best:** add an IR-cut filter in front of the sensor. A small piece of
  650 nm IR-cut glass works.
- **Software:** set `tuning = "imx477_noir.json"` in `config.toml`. This
  improves JPEG colour a lot, though foliage and dark fabrics can still look off.

## Memory

Full-resolution capture (12 MP RGB + 12-bit raw, two buffers) needs about
130 MB of contiguous (CMA) memory. If `info` shows less than ~160 MB, edit
`/boot/firmware/config.txt`: change `dtoverlay=vc4-kms-v3d` to
`dtoverlay=vc4-kms-v3d,cma-192`, then reboot. Keep HEIF off if memory is tight:
encoding a 12 MP HEIF on a Zero 2 W takes several seconds and a lot of RAM.

## Power

The 300 mAh cell holds about 1.1 Wh. A Zero 2 W plus the sensor draws roughly
1–1.5 W, so expect **around 45 minutes** per charge. Every photo is synced to
the SD card as it's written, so a dead battery shouldn't lose shots. Only
**GPIO3 (header pin 5)** can wake a halted Pi. If you later add a power
button that turns the Pi on as well as off, wire it to pin 5 + GND and add
`dtoverlay=gpio-shutdown,gpio_pin=3` to `config.txt`.

## Layout

```
konicaback/camera.py    sensor control, lens-shot detection, raw merging
konicaback/app.py       main loop and saving
konicaback/hardware.py  buttons, RGB status LED, pin finder
konicaback/storage.py   file numbering, background HEIF encoder
deploy/                 Pi installer + systemd unit
tests/test_logic.py     hardware-free tests (python3 tests/test_logic.py)
```
