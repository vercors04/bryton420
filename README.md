# bryton420

Converts GPX routes to the Bryton Rider 420 route format (`.fit`). The Rider derives its navigation files (`.smy`, `.tinfo`, `.track`) from the `.fit` file placed in `PlanTrip` and gives turn-by-turn instructions.

No Bryton app, Google services or Bluetooth required: transfer is by USB cable. No Python dependencies.

## Modes

| | Offline | Online |
|---|---|---|
| Input | GPX that already contains instructions | Track only (Komoot, Strava, recorded or hand-drawn GPX) |
| Turns | Taken from the GPX | Recomputed by BRouter on the track |
| Street names | From the GPX, if present | Added by Valhalla |
| Network | None | Track sent to brouter.de and valhalla1.openstreetmap.de |

Default: offline if the GPX contains turns, online otherwise (a GPX with only start and end points runs online). `--offline` and `--online` override this. `--online` also adds street names to a BRouter export, which has none.

## Route planning tools

| Tool | Platform | Export | Mode |
|---|---|---|---|
| bikerouter.de or brouter-web | Computer, phone browser | GPX with `turnInstructionMode = osmand-style` | Offline (recommended, nothing sent) |
| cycle.travel | Computer | GPX track, without "Announce turns in advance" | Online |
| OsmAnd (F-Droid) | Phone, offline | Plan a route, save as GPX | Online |
| VisuGPX, Komoot, Strava | Any | GPX track | Online |

The cycle.travel "GPX route" and the native OsmAnd GPX contain instructions in a format not yet supported. Export the track instead; online mode recomputes everything. The Rider does not display points of interest.

## 1. Prepare the GPX

### Offline: BRouter with instructions

BRouter is open source, based on OpenStreetMap, requires no account and has no tracking. Web interface: brouter.de/brouter-web or bikerouter.de.

1. Place waypoints and choose a bike profile (trekking works well).
2. In the Profile tab, set `turnInstructionMode` to `osmand-style`. The default, `auto-choose`, exports the track only.
3. Export as GPX. The export dialog should state "Includes turn instructions".

`locus-style` and `gpsies-style` also work, as do "directions" exports from openrouteservice.org and PlotARoute (waypoints and track), which include street names.

BRouter provides no street names and reports many junctions (example: 87 instructions over 16 km, 21 of them within 20 m of the next). `turnInstructionCatchingRange` (Profile tab, 40 m by default) merges instructions closer than this distance.

### Online: any track

Export the GPX from Komoot, Strava or another source and convert it directly. The tool sends the track to two OpenStreetMap-based services:

- Valhalla: snaps the track to the map and returns the street names.
- BRouter: recomputes the route with a waypoint every few metres along the track, which forces it to follow the track, and derives the turns.

Recorded or hand-drawn tracks do not match roads exactly: GPS noise makes them zigzag by a few metres. The tool detects this (track more than 1 m from roads on average), snaps the track to roads with Valhalla before passing it to BRouter, and reports it. Noisy elevations are smoothed.

BRouter may leave the track on a road its bike profile avoids and place turns on its own detour. The tool detects these sections (track more than 50 m from the BRouter route), uses Valhalla instructions there and BRouter instructions elsewhere, and reports the distance affected. If BRouter refuses the track (for example a section closed to bikes) or deviates over more than half of it, Valhalla computes all turns. Server overload refusals are retried twice.

### Measured quality

Cycling route in Lyon, 11.4 km, 48 known manoeuvres and 33 street names. Each method sees only the track.

| Method | Turns found | Correct side | Street names |
|---|---|---|---|
| BRouter + Valhalla (online mode) | 48/48 | 46/48 | 30/33 |
| Online mode, same route recorded (simulated 4 m GPS noise, one point every 5 m) | 41/48 | 38/41 | 29/33 |
| Valhalla only (fallback) | 39/48 | 36/39 | 26/33 |
| Track shape only, no network | 33/48 | 31/33 | 0/33 |

On the recorded track, 2 of the 7 missed turns are announced 30 m from their position and 5 are missed. A track drawn in a planner remains the best input.

On 7 km of a Lyon to Grenoble route where BRouter leaves the track for 1.7 km (adding about twenty turns), online mode finds all 7 real manoeuvres on the correct side and no phantom turns. Valhalla alone finds 4.

These are two test routes. Review the instruction list with `doctor` before riding.

### Privacy

Online mode sends the full track (positions only, no timestamps or elevations) to:

- valhalla1.openstreetmap.de, operated by FOSSGIS for the OpenStreetMap community. Its terms allow at most one request per second, and requests are logged by the server.
- brouter.de, the public BRouter server.

A long ride is split into several requests at least one second apart: about 1 to 2 minutes per 150 km of recorded track. Requests are printed as they are sent.

To keep a track private, use `--offline`, or self-host BRouter and Valhalla and pass their addresses with `--brouter URL` and `--valhalla URL`.

## 2. Convert

Requires Python 3.10 or newer. Run from the repository folder.

```
python -m bryton_route doctor my-ride.gpx
```

Prints what will be written, without writing anything: points, distance, elevation gain, and each instruction with the distance to the next one and the street name. Run it first: a file the Rider does not accept is silently ignored.

```
python -m bryton_route convert my-ride.gpx
python -m bryton_route convert my-ride.gpx -o E:\PlanTrip
```

Writes `my-ride.fit` next to the GPX, or directly into `PlanTrip` on the connected Rider. The file name is the route name on the Rider. `--name "Route name"` sets a different one.

| Option | Effect |
|---|---|
| `--name NAME` | Route name on the Rider, and name of the `.fit` file |
| `--offline` | Use only the instructions in the GPX; send nothing |
| `--online` | Recompute turns and street names online even if the GPX has instructions |
| `--tolerance M` | Track simplification, 2 m by default; 0 keeps all points (instructions never move) |
| `--brouter URL`, `--valhalla URL` | Use other servers, for example self-hosted |
| `--allow-no-cues` | With `--offline`, convert a GPX without instructions to a plain line |

`pip install .` installs a `bryton-route` command usable from anywhere.

## 3. Load onto the Rider

1. Connect the Rider by USB and copy the `.fit` file into `PlanTrip`.
2. Eject and unplug.
3. Switch the Rider fully off, then on.
4. In the Routes menu, select the new route. The Rider then generates its navigation files in `Tracks`.

To check the result, reconnect the Rider and run:

```
python -m bryton_route rider E:\
```

For each route in `PlanTrip`, this reports whether it was imported (the Rider created its files in `Tracks` with the same instructions and points as the `.fit`). To read the Rider's instruction table in detail:

```
python -m bryton_route inspect "E:\Tracks\my-ride.tinfo"
```

### First test ride

The format was checked byte by byte against a file from the Bryton app, but some behaviour is only visible on the Rider's screen. Do a short test before a real ride.

1. On bikerouter.de (osmand-style), draw a 3 to 5 km loop with at least one right turn, one left turn, a roundabout and a fork.
2. Run `doctor`, then `convert loop.gpx -o E:\PlanTrip`, eject, power cycle and select the route. After reconnecting, `rider E:\` should report the files as identical to the `.fit`.
3. While riding, check for each instruction: announced, correct arrow, readable street name, correct remaining distance, correct location.
4. Behaviours not yet observed on a Rider: the roundabout arrow (single direction, preceded by the exit number); closely spaced instructions (increase `turnInstructionCatchingRange` if they are a nuisance); what the Rider does when leaving and rejoining the route.
5. Repeat with a track without instructions (online mode).
6. Then test a long route (100 km or more, several hundred instructions; the reference file had 96) and route replacement (a `.fit` with the same name copied over the old one). If the Rider keeps the old route, delete it from the Rider menu or use another `--name`.

If an instruction is wrong, note the kilometre: `doctor` and `inspect` show what was written there.

## Android without a computer

Requires Termux (from F-Droid; the Play Store version is no longer updated) and a USB-C male to USB-A female OTG adapter to connect the Rider cable.

One-time setup in Termux:

```
pkg install python git
termux-setup-storage
git clone https://github.com/vercors04/Bryton420
cd Bryton420 && pip install .
mkdir -p ~/bin && cp termux/termux-file-editor ~/bin/ && chmod +x ~/bin/termux-file-editor
```

`termux-setup-storage` requests access to the phone's files: accept. The last line installs the script that runs the conversion when a GPX is shared to Termux.

For each ride:

1. In the app or site that produced the route, share the GPX to Termux and choose Edit.
2. Name the route (or keep the suggested name). The `.fit` is saved to the phone's Download folder. A track without instructions needs a connection (online mode).
3. Connect the Rider with the OTG adapter. In the Files app, copy the `.fit` from Download to `PlanTrip` on the Rider (Termux cannot write to USB storage itself).
4. Eject the Rider (USB notification, or Settings > Storage), unplug, switch it off and on, and select the route.

Update: `cd Bryton420 && git pull && pip install .`

## Notes

- Roundabouts: Bryton uses its own codes, probably "exit 1 to 4", but their display is unconfirmed. A roundabout is shown as a single arrow from the entry road to the exit road, with the exit number and exit street: `(2) Avenue Berthelot`. The angle provided by BRouter is ignored (it reported "left" even for a straight exit). U-turns are shown as a sharp turn.
- Street names: 32 bytes maximum (about 30 characters). A name that is too long loses its road number first (`Avenue Jean Moulin, D 306` becomes `Avenue Jean Moulin`), then its last words, never half a word. Accents are supported.
- Several tracks in one GPX: joined end to end if they follow each other (stages of one route); otherwise only the longest is converted, and the tool reports it.
- Recorded elevations: smoothed when noisy. The tool prints the elevation gain before and after.
- Points of interest (passes, supplies): not supported.
- Widely spaced tracks (one point every 300 m) cut corners on the screen: `doctor` reports it.

## Format and code

The output file reproduces, message by message, the structure of the file written by the Bryton Active app. It derives from the analysis of one reference pair, a `.fit` written by the app and the files the Rider 420 generated from it, kept in `tests/data/ex_bon`. Field details are in `doc/format.md`.

```
bryton_route/
  route.py         GPX to route: track, instruction placement, warnings
  cues.py          instruction parsing (BRouter and ORS codes, symbols, FR/EN/DE phrases)
  gpx.py           GPX XML reading
  online.py        online mode: snapping, BRouter turns, Valhalla names, fallbacks
  brouterweb.py    BRouter recomputation of a track, sections it does not follow
  valhalla.py      Valhalla snapping: street names, snapped track, fallback turns
  web.py           network access: User-Agent, one request per second, errors
  simplify.py      track simplification without moving instructions
  bryton.py        Bryton route format (write and read)
  fit.py           generic FIT container
  geo.py           WGS-84 geodesy
  cli.py           command line
termux/            conversion of a GPX shared to Termux on Android
tests/             python -m pytest (no network: server responses are recorded)
```

Based on the work of matheus0312/BrytonUtilities and Edward-Eth/BrytonUtilities24. Public domain (Unlicense). Map data © OpenStreetMap contributors.

