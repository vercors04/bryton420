"""Ligne de commande.

    bryton-route doctor  sortie.gpx              # ce qui sera écrit, sans rien écrire
    bryton-route convert sortie.gpx              # écrit sortie.fit à côté du GPX
    bryton-route convert sortie.gpx -o E:\\PlanTrip
    bryton-route inspect sortie.fit --cues       # relit un .fit, .tinfo ou .track

Deux modes. Hors ligne, les instructions du GPX sont utilisées telles quelles.
En ligne, elles sont calculées depuis la trace : calage et noms de rue par
Valhalla, virages par BRouter. Par défaut, le mode en ligne n'est utilisé que
pour un GPX sans virages.
"""

import argparse
import os
import re
import sys
import urllib.parse
from collections import Counter
from contextlib import contextmanager
from itertools import accumulate

from . import __version__, brouterweb, bryton, geo, online, valhalla, web
from .model import CueType
from .route import NO_CUES, ONLY_ENDS, UnsupportedGpx, has_turns, read_gpx_file
from .simplify import DEFAULT_TOLERANCE_M, simplify

TYPE_NAMES = {
    CueType.STRAIGHT: "tout droit",
    CueType.RIGHT: "à droite",
    CueType.LEFT: "à gauche",
    CueType.SLIGHT_RIGHT: "légèrement à droite",
    CueType.SLIGHT_LEFT: "légèrement à gauche",
    CueType.SHARP_RIGHT: "serré à droite",
    CueType.SHARP_LEFT: "serré à gauche",
    CueType.KEEP_RIGHT: "rester à droite",
    CueType.KEEP_LEFT: "rester à gauche",
    CueType.END_OF_ROUTE: "arrivée",
}

_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')


def type_name(kind):
    return TYPE_NAMES.get(kind, f"code 0x{int(kind):02X}")


def _km(metres):
    return f"{metres / 1000:.2f} km".replace(".", ",")


def _host(url):
    return urllib.parse.urlsplit(url).netloc or url


@contextmanager
def _counting_requests():
    """Show the requests as they go out (live on a terminal), then their count."""
    counts = Counter()
    live = sys.stdout.isatty()

    def line():
        return "requêtes      : " + ", ".join(f"{host} {n}" for host, n in counts.items())

    def show(host):
        counts[host] += 1
        if live:
            print("\r" + line(), end="", flush=True)

    web.on_request = show
    try:
        yield
    finally:
        web.on_request = None
        if counts:
            print("\r" + line() if live else line())


def _prepare(args):
    route = read_gpx_file(args.gpx, allow_no_cues=True)
    if args.online or (not args.offline and not has_turns(route)):
        if route.cues and not args.online:
            route.warnings.append(ONLY_ENDS)
        print(
            f"en ligne      : trace envoyée à {_host(args.valhalla)} (calage, noms de rue) "
            f"et à {_host(args.brouter)} (virages)"
        )
        with _counting_requests():
            route = online.add_cues(route, brouter=args.brouter, valhalla_server=args.valhalla)
    elif not route.cues:
        if not args.allow_no_cues:
            raise UnsupportedGpx(NO_CUES.format(source=route.source))
        route.warnings.append("aucune instruction : le Rider n'affichera que la ligne")
    elif not has_turns(route):
        route.warnings.append("aucun virage dans le GPX, seulement départ et arrivée")
    length = sum(geo.path_lengths(route.points))
    thinned = simplify(route, args.tolerance)
    return route, thinned, bryton.encode(thinned, total_distance=length)


def _print_cues(route, distances):
    along = [0.0, *accumulate(geo.path_lengths(route.points))]
    print("\n    km   suivante  instruction           rue")
    for cue, distance in zip(route.cues, distances):
        print(
            f"  {along[cue.index] / 1000:6.2f}  {distance:7d} m  "
            f"{type_name(cue.type):20s}  {cue.label}"
        )


def _report(route, thinned, data, show_cues):
    written, summary = bryton.decode(data)
    counts = Counter(type_name(cue.type) for cue in written.cues)
    points = f"{len(route.points)} points"
    if len(thinned.points) != len(route.points):
        points += f" → {len(thinned.points)} après simplification"
    print(f"source        : {route.source}")
    print(f"tracé         : {points}")
    print(
        f"parcours      : {_km(summary['distance_m'])}, "
        f"+{summary['ascent_m']} m / -{summary['descent_m']} m"
    )
    print(
        f"instructions  : {len(written.cues)} ("
        + ", ".join(f"{n} {name}" for name, n in counts.most_common())
        + ")"
    )
    for warning in route.warnings:
        print("attention     : " + warning.replace("\n", "\n                "))
    if show_cues:
        _print_cues(written, summary["cue_distances"])


def _target(output, gpx_path, name):
    """A .fit named after the route, in ``output`` or next to the GPX, unless
    ``output`` is itself a file name. The file name is the route name on the Rider."""
    if output is not None and not os.path.isdir(output):
        return output
    folder = output if output is not None else os.path.dirname(os.path.abspath(gpx_path))
    filename = _UNSAFE_FILENAME.sub("-", name).strip() or "itineraire"
    return os.path.join(folder, filename + ".fit")


def cmd_convert(args):
    route, thinned, data = _prepare(args)
    _report(route, thinned, data, show_cues=False)
    target = _target(args.output, args.gpx, args.name or route.name)
    replaced = os.path.exists(target)
    with open(target, "wb") as handle:
        handle.write(data)
    print(f"\nécrit         : {target} ({len(data)} octets)" + (", remplacé" if replaced else ""))
    if os.path.basename(os.path.dirname(os.path.abspath(target))).lower() == "plantrip":
        print(
            "ensuite       : éjecte le Rider, éteins-le puis rallume-le,\n"
            "                et ouvre l'itinéraire depuis le menu Itinéraires."
        )
    else:
        print(
            "ensuite       : copie-le dans PlanTrip/ sur le Rider, éjecte, éteins puis "
            "rallume,\n                et ouvre l'itinéraire depuis le menu Itinéraires."
        )
    return 0


def cmd_doctor(args):
    route, thinned, data = _prepare(args)
    _report(route, thinned, data, show_cues=True)
    return 0


def cmd_inspect(args):
    with open(args.path, "rb") as handle:
        data = handle.read()
    extension = os.path.splitext(args.path)[1].lower()

    if extension == ".tinfo":
        entries = bryton.read_tinfo(data)
        print(f"{len(entries)} instructions enregistrées par le Rider")
        _print_cues_from_device(entries)
        return 0
    if extension == ".track":
        points = bryton.read_track(data)
        print(f"{len(points)} points enregistrés par le Rider")
        return 0

    route, summary = bryton.decode(data)
    crc = "ok" if summary["crc_ok"] else "INVALIDE"
    print(f"version       : profil {summary['profile_version']}, CRC {crc}")
    print(f"tracé         : {len(route.points)} points (en-tête : {summary['declared_points']})")
    print(f"instructions  : {len(route.cues)} (en-tête : {summary['declared_cues']})")
    print(
        f"parcours      : {_km(summary['distance_m'])}, "
        f"altitude {summary['alt_min_m']:.0f}–{summary['alt_max_m']:.0f} m"
    )
    if summary["ascent_m"] is not None:
        print(f"dénivelé      : +{summary['ascent_m']} m / -{summary['descent_m']} m")
    if args.cues:
        _print_cues(route, summary["cue_distances"])
    return 0


def _imported(tracks, name, route):
    """What the Rider built in Tracks/ from PlanTrip/<name>.fit, in words."""
    tinfo = os.path.join(tracks, name + ".tinfo")
    track = os.path.join(tracks, name + ".track")
    if not (os.path.exists(tinfo) and os.path.exists(track)):
        return (
            "pas encore importé : éteins et rallume le Rider, puis choisis-le "
            "dans le menu Itinéraires"
        )
    with open(tinfo, "rb") as handle:
        cues = bryton.read_tinfo(handle.read())
    with open(track, "rb") as handle:
        points = bryton.read_track(handle.read())
    same = [cue for cue, _ in cues] == route.cues and len(points) == len(route.points)
    verdict = "identiques au .fit" if same else "DIFFÉRENTS du .fit"
    return f"importé : {len(cues)} instructions, {len(points)} points, {verdict}"


def cmd_rider(args):
    plantrip = os.path.join(args.path, "PlanTrip")
    tracks = os.path.join(args.path, "Tracks")
    if not os.path.isdir(plantrip):
        raise ValueError(f"pas de dossier PlanTrip dans {args.path} : est-ce bien le Rider ?")
    names = sorted(f for f in os.listdir(plantrip) if f.lower().endswith(".fit"))
    if not names:
        print("PlanTrip est vide")
    for filename in names:
        name = os.path.splitext(filename)[0]
        with open(os.path.join(plantrip, filename), "rb") as handle:
            data = handle.read()
        try:
            route, summary = bryton.decode(data)
        except ValueError as exc:
            print(f"{filename} : illisible ({exc})")
            continue
        crc = "" if summary["crc_ok"] else ", CRC INVALIDE"
        print(
            f"{filename} : {_km(summary['distance_m'])}, {len(route.cues)} instructions, "
            f"{len(route.points)} points{crc}"
        )
        print("  " + _imported(tracks, name, route))
    return 0


def _print_cues_from_device(entries):
    print("\n  point  suivante  instruction           rue")
    for cue, distance in entries:
        print(f"  {cue.index:5d}  {distance:7d} m  {type_name(cue.type):20s}  {cue.label}")


def _parser():
    parser = argparse.ArgumentParser(
        prog="bryton-route",
        description="GPX d'itinéraire → fichier de navigation pour Bryton Rider 420.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    def gpx_command(name, summary, func):
        command = commands.add_parser(name, help=summary, description=summary)
        command.add_argument("gpx", help="le fichier GPX")
        command.add_argument(
            "--tolerance",
            type=float,
            default=DEFAULT_TOLERANCE_M,
            metavar="M",
            help="écart maximal en mètres toléré en allégeant le tracé ; "
            "0 garde tous les points (défaut : %(default)s)",
        )
        mode = command.add_mutually_exclusive_group()
        mode.add_argument(
            "--offline",
            action="store_true",
            help="n'utiliser que les instructions du GPX ; rien n'est envoyé en ligne",
        )
        mode.add_argument(
            "--online",
            action="store_true",
            help="calculer virages et noms de rue en ligne même si le GPX a des instructions",
        )
        command.add_argument(
            "--brouter",
            default=brouterweb.DEFAULT_SERVER,
            metavar="URL",
            help="serveur BRouter pour les virages (défaut : %(default)s)",
        )
        command.add_argument(
            "--valhalla",
            default=valhalla.DEFAULT_SERVER,
            metavar="URL",
            help="serveur Valhalla pour les noms de rue (défaut : %(default)s)",
        )
        command.add_argument(
            "--allow-no-cues",
            action="store_true",
            help="avec --offline, convertir un GPX sans instructions (ligne seule)",
        )
        command.set_defaults(func=func)
        return command

    convert = gpx_command("convert", "écrit le .fit à copier dans PlanTrip", cmd_convert)
    convert.add_argument(
        "-o", "--output", help="fichier .fit de sortie, ou dossier (par exemple PlanTrip du Rider)"
    )
    convert.add_argument(
        "--name",
        help="nom de l'itinéraire sur le Rider, qui est celui du fichier .fit "
        "(défaut : nom du GPX ; ignoré si -o désigne un fichier)",
    )
    gpx_command("doctor", "montre ce qui serait écrit, sans rien écrire", cmd_doctor)

    inspect = commands.add_parser("inspect", help="relit un .fit, .tinfo ou .track")
    inspect.add_argument("path")
    inspect.add_argument("--cues", action="store_true", help="liste les instructions")
    inspect.set_defaults(func=cmd_inspect)

    rider = commands.add_parser(
        "rider", help="vérifie ce que le Rider branché a importé de PlanTrip"
    )
    rider.add_argument("path", help="la racine du Rider, par exemple E:\\")
    rider.set_defaults(func=cmd_rider)
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except UnsupportedGpx as exc:
        print(f"impossible de convertir ce fichier :\n{exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"erreur : fichier introuvable : {exc.filename}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(f"erreur : accès refusé : {exc.filename}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
