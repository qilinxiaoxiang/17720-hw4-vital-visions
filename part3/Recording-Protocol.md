# Part 3 recording protocol

You will make 8 recordings, one per combination of delta alpha (0.1, 0.5) and distance (20, 40, 80, 120 cm), plus one eye check. Each recording takes under 2 minutes and covers every symbol rate (5, 7.5, 10, 15 and 30 bps). Plan on about 75 to 90 minutes hands-on, plus about 15 minutes of unattended decoding.

The transmitter is `Code/part3/transmitter.html`. The phone records in Slo-mo at 1080p 240 fps, and the Mac decodes the videos afterwards. All paths below are relative to the HW4 folder `Assignments/HW4-Vital-Visions/`.

## What you need

- The MacBook Pro, plugged into power.
- The iPhone 16 Pro on a tripod or a stable stack of books.
- A tape measure, plus masking tape to mark the four distances.

## 1. One-time setup (about 15 minutes)

### Mac

1. **Refresh rate.** In System Settings > Displays, set Refresh Rate to ProMotion. Use the built-in display, not the external monitor.
2. **Brightness.** In the same pane, turn off "Automatically adjust brightness" and True Tone. Set brightness to about 75% and do not touch it again until all recordings are done. Write the level down.
3. **Night Shift.** In Displays > Night Shift, set Schedule to Off.
4. **Power and interruptions.**
   - In System Settings > Battery, set Low Power Mode to Never. Low Power Mode may lower the frame rate.
   - Turn on Do Not Disturb (Control Center > Focus) so no notification appears over the picture.
5. **Open the transmitter.**
   - Open the transmitter in Google Chrome. In Terminal, run `open -a "Google Chrome" Code/part3/transmitter.html`, or drag the file onto Chrome.
   - The panel must say **"Measured display refresh: 120 Hz ... Good: 120 Hz ProMotion."** If it shows 60 Hz, fix the display settings above and click "Re-measure refresh".
   - Use Chrome, not Safari: Safari usually runs animations at 60 Hz.

### Room

6. Keep the room lighting the same for all recordings, for example ceiling lights on and blinds closed.
7. No lamp should shine directly on the screen. Do not walk between the phone and the screen during a recording.

### iPhone

8. **Formats.** In Settings > Camera > Formats, choose **High Efficiency**. 240 fps Slo-mo requires it.
9. **Slo-mo quality.** In Settings > Camera > Record Slo-mo, choose **1080p HD at 240 fps**.
10. **Lens switching.**
    - In Settings > Camera > Record Video, turn on **Lock Camera** if your iOS version shows it. This stops the phone from switching lenses mid-recording.
    - In Settings > Camera, turn on **Macro Control**. A flower button then appears at close range, and you can keep auto-macro **off** at 20 cm.
11. **Interruptions.** Turn on Airplane Mode or Do Not Disturb so a call cannot stop a recording.
12. **Free space.** Each 2-minute 240 fps clip is a few hundred MB.

### Distances

13. Measure from the screen surface to the front of the phone's camera lens, along the line straight out from the screen center.
14. Put tape marks on the desk at 20, 40, 80 and 120 cm.
15. Mount the phone in landscape with the lens at the height of the screen center, pointing straight at it.
16. Before each recording, recheck the distance with the tape measure.

## 2. Recording order

Record in this order, so the phone moves as little as possible and the first file is the shakedown test:

1. `p3_da050_d040` (delta alpha 0.5, 40 cm). This is the shakedown: decode it right away (section 4) before doing the rest.
2. `p3_da010_d040`
3. `p3_da010_d020`
4. `p3_da050_d020`
5. `p3_da010_d080`
6. `p3_da050_d080`
7. `p3_da010_d120`
8. `p3_da050_d120`

In each name, `da010` means delta alpha 0.10, `da050` means 0.50, and `d020` means 20 cm. The decoder reads delta alpha and distance from the name, so keep the names exact.

## 3. Steps for each recording (about 4 to 5 minutes each)

1. Place the phone at the distance mark and point it at the screen center.
2. **Set up the transmitter.** In the transmitter panel, set:
   - Mode: **sweep (all rates)**
   - Camera profile: **cam240**
   - Delta alpha: **0.1 or 0.5**
   - Recording label: the file name from the list, for example `p3_da010_d020`

   Leave every other field at its default. The info box should say "f0 (bit 0) = 30 Hz, f1 (bit 1) = 60 Hz; symbol rate(s) 5, 7.5, 10, 15, 30 bps ... transmission 91.1 s".
3. Click **Start (full screen)**. The picture now fills the screen at its brightest level, with the line "Frame the screen, lock exposure, start recording, then press SPACE".
4. **Frame the shot.** On the phone, open Camera, swipe to **Slo-mo**, and check that **240** and **1x** are shown.
   - At 40 cm and farther, fit the whole screen inside the frame.
   - At 20 cm, the screen will be larger than the frame. That is fine; keep the picture in the middle of the frame.
   - If a flower (macro) button appears and is yellow, tap it so it turns off.
5. **Lock exposure.** Long-press on the picture until **AE/AF LOCK** appears. Then drag the sun slider down a little, until the sun disc in the picture is no longer pure white. Do not tap the screen after this.
6. Start the phone recording, step back, and press **Space** on the Mac. A 5-second countdown runs, then the transmission, about 91 s. Nothing moves on screen except the picture's brightness. Keep still and do not touch the table.
7. When the **DONE** panel appears, stop the phone recording.
8. Click **Download transmit log (JSON)**. This saves `<label>_txlog.json` to Downloads.
9. Write down a line for this recording (see section 6).
10. Click **Back to setup** for the next one.

If anything goes wrong (the phone moved, someone walked by, a notification appeared), press Esc and redo that recording. Afterwards, delete the bad clip on the phone so the file order stays clean.

## 4. Transfer and check

1. **Select the videos.** In Photos on the iPhone, select the recordings.
2. **Send the originals.** Tap Share, then **Options** at the top, and turn on **All Photos Data**. Then AirDrop to the Mac. Without this option, Slo-mo may not arrive as the 240 fps original.
3. **Rename.** The files arrive in Downloads as `IMG_xxxx.MOV`. Rename them in recording order to the names above with a lowercase `.mov`, for example `p3_da050_d040.mov`. Then move them into `Data/part3/`.
4. **Move the logs.** Move each `p3_da..._txlog.json` from Downloads into `Data/part3/` as well. Keep each log's name the same as its video name, with `_txlog.json` added.
5. **Check the files.** Every file must show about 240 fps, and the creation times must follow the recording order:

   ```bash
   cd part3
   python3 decode_video.py --probe-only ../../Data/part3/p3_da*.mov
   ```

6. **Shakedown decode.** Decode the shakedown file first:

   ```bash
   nice -n 15 python3 decode_video.py ../../Data/part3/p3_da050_d040.mov --out-root ../../Analysis/Part3/results
   ```

   A good result has four signs:
   - "fps 240";
   - "pilot at ... s";
   - a BER line for each rate;
   - no WARNING lines.

   Open `Analysis/Part3/results/p3_da050_d040/roi.png` and check that the orange outline covers the screen. If the ROI is wrong, re-run with `--roi x,y,w,h`, the screen rectangle in the video's pixels.

After all 8 files are in place, decode the rest and make the report figures. The full commands are in `Code/part3/README.md`.

## 5. Eye check (about 4 minutes)

This test records when the alpha changes become visible to you. Nothing is filmed.

1. Sit at your normal laptop viewing distance (about 50 cm), with the same brightness and room lighting as the recordings.
2. In the transmitter, choose Mode **eye check** and click Start.
3. There are 22 trials of 5 s each, in shuffled order:
   - both delta alpha values;
   - each BFSK symbol rate;
   - steady flicker at 10, 15, 20, 30 and 60 Hz;
   - one control with no modulation.
4. Press **Space** to watch each trial. Then answer **Y** if you clearly saw flicker or change, **F** if it was faint or you are unsure, or **N** if you saw none. **R** replays the trial.
5. At the end, click **Download**. This saves `p3_eyecheck.json`. Move it to `Data/part3/`.
6. Also write one or two sentences on what you noticed, for example "0.5 flickers at every rate; 0.1 visible only at 10 to 20 Hz".

## 6. What to write down

Keep these notes in `Data/part3/Recording-Notes.txt`.

**Once:**
- the date;
- the Mac brightness level;
- the room lighting;
- the iPhone camera settings you used.

**For each recording:**
- the file name;
- the IMG number;
- the start time;
- the dropped-frames figure from the DONE panel;
- anything unusual, such as movement, a notification or a refocus.

**For the eye check:** your free-text impression.

## Time estimate

- Setup, distance marks and phone settings: 15 minutes.
- 8 recordings at 4 to 5 minutes each (positioning, exposure lock, 1 minute 40 seconds of recording, saving the log): 35 to 40 minutes.
- Shakedown decode after the first recording: 5 minutes.
- Eye check: 4 minutes.
- AirDrop, renaming and the probe check: 10 minutes.
- Decoding all 8 files: about 1.5 minutes each, about 12 to 15 minutes in total, unattended.

That is about 75 to 90 minutes hands-on, plus the decoding.

## Optional words demo

This shows text sent from screen to phone.

1. In the transmitter, choose Mode **text (words demo)** and set Delta alpha 0.5.
2. Set Symbol length N to **8** (15 bps).
3. Enter your words in Text, for example `HELLO VITAL VISIONS CMU`, and keep Repeats at 2.
4. Use the label `p3_text_demo`.
5. Record at 40 cm exactly as above. It takes about 75 s.
6. Decode with `decode_video.py ../../Data/part3/p3_text_demo.mov --out-root ../../Analysis/Part3/results`. The decoder prints the words it received, and it never uses the typed text to decode them.

If a third camera is available, film the screen and the terminal output for the demo video.

## Scope note for the report

The receiver here is the phone camera recording video, decoded afterwards in Python on the Mac. The decoder processes frames causally, symbol by symbol, so the same logic could run live. A live on-phone decoder is not part of this setup.
