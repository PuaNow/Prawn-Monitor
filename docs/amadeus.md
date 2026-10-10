# Recording for AMADEUS

[AMADEUS](https://amadeus.jpmyrmecol.com/) tracks animals in a video and writes a CSV. It is licensed AGPL-3.0-only. Install it yourself from the project site or from `https://github.com/jpmyrmecol/AMADEUS`. Prawn Monitor does not ship AMADEUS, does not link to it, and does not load its weights. The two programs meet only in files you carry between machines: an MP4 going out, a CSV coming back.

The tracking PC for this farm is an NVIDIA GeForce RTX 4060 with 8 GB of VRAM. AMADEUS recommends 8 GB or more. That card meets the recommendation. Training still depends on resolution, animal count, and batch size. If AMADEUS runs out of memory, lower the training batch size in Advanced Tracking. That is the first check their manual lists for an out-of-memory error.

## What the node records

Scheduled times live in the node config under `recording`. The example records at 08:00 and 16:00 Pacific, for 180 seconds, at the camera's configured size and frame rate. Each clip is an H.264 MP4 plus a JSON sidecar:

- tank id, camera id, start time, duration, frame count
- width, height, frames per second
- `exposure: "fixed"` and `auto_exposure: false`
- the requested exposure time
- millimeters per pixel
- a note that the clip is an overhead view for AMADEUS

You can also press Record on the dashboard. That asks the hub to ask the node, so the phone never has to reach the Pi directly.

Auto exposure is the main thing that breaks background subtraction. The node turns it off on a Pi camera (`AeEnable` false, `ExposureTime` set) and requests manual exposure on a USB camera. Watch a clip before you trust it: brightness should stay put when a shrimp crosses the frame.

## How to film shrimp for this tracker

AMADEUS's own manual asks for a fixed overhead camera, animals moving on a plane, stable light, a background that does not move, and enough frames where animals are separated so it can build training images. Head direction needs a body axis and a front that looks different from the rear.

For this nursery:

- Bolt the camera. Do not use a tripod that gets bumped at feeding time.
- Light the tank the same way every session. A light on a timer is better than a window.
- Keep the background still. Do not record during a water change or while a net is in the frame.
- Prefer 720p or 1080p at 30 fps for a real tracking clip. A 4K frame is a lot of VRAM on an 8 GB card and the animals do not need it.
- Three minutes is a reasonable daily clip. A dense nursery may need a longer clip, or a separate session when you can see gaps between animals, because AMADEUS learns from isolated individuals. If the tank is a solid layer of shrimp, tracking will be poor no matter the software.
- Juvenile whiteleg shrimp are small, and the rostrum is not always obvious. Run Easy Tracking with direction estimation first. If front and rear stay confused, run again with "Without direction estimation". Body length in Prawn Monitor uses the long side of the oriented box, which still exists in that mode.
- Overlap in a nursery is usually Heavy. Use Very heavy only when animals pile on top of each other for much of the clip. That path adds embedding-based identity correction and takes longer.
- Answer "yes" to backward movement if you see shrimp back up. The manual uses that to drop direction labels it does not trust.

Train and analyze under the same camera, light, and water depth. A clip from a different mount is a different problem.

## Move files without the internet

The Pi does not run the GPU. Copy the MP4 and the JSON to the 4060 machine on a USB stick or with `rsync` on the LAN:

```bash
rsync -a pi@192.168.1.21:/var/lib/prawn-monitor/nursery/clips/ ./clips/
```

On the GPU machine, open AMADEUS, Easy Tracking, pick the MP4, and follow their manual: segmentation, then the three questions, then Processing. The final CSV lands in the session `results/` folder. The usual columns are:

```text
frame,cx0,cy0,w0,h0,heading0,cx1,cy1,w1,h1,heading1,...
```

Coordinates are pixels, origin at the top left. A run without direction estimation omits `heading`. Either file imports here.

Bring the CSV back to the hub. On the Activity tab, choose the file, set frames per second to the clip's fps, and set millimeters per pixel from the sidecar (or from [calibration.md](calibration.md)). Crowding distance defaults to 40 mm. The hub then charts:

- mean speed (mm/s) from center to center
- cumulative distance
- mean nearest-neighbor distance, which is the crowding trace (smaller means tighter)
- approximate body length, the long side of the box times millimeters per pixel

That length is not a caliper measurement. A curved shrimp or a box that includes the antennae will read long. Use it to watch a group over weeks, and keep the camera calibration fixed while you do.

Cite AMADEUS when you publish tracking from it. Their site is `https://amadeus.jpmyrmecol.com/`. The preprint is linked from that site.
