"""WGS-84 geodesic helpers.

Pure standard library on purpose: the whole core must stay importable under
Pyodide (browser) and Termux without a single compiled dependency. This module
replaces the ``geopy`` calls of the legacy ``BrytonUtilities`` package; values
agree with ``geopy.distance.distance`` to well under a millimetre over the
segment lengths found in cycling routes (see tests/test_geo.py).
"""

from math import atan, atan2, cos, degrees, radians, sin, sqrt, tan

# WGS-84 ellipsoid.
_A = 6378137.0  # semi-major axis, metres
_F = 1 / 298.257223563  # flattening
_B = _A * (1 - _F)  # semi-minor axis, metres


def geodesic_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two WGS-84 positions given in degrees.

    Vincenty inverse solution. Cycling routes never contain the near-antipodal
    pairs where Vincenty fails to converge, but we fall back to the spherical
    haversine distance rather than raise if it ever happens.
    """
    if lat1 == lat2 and lon1 == lon2:
        return 0.0

    phi1, phi2 = radians(lat1), radians(lat2)
    delta_lon = radians(lon2 - lon1)

    # Reduced latitudes on the auxiliary sphere.
    u1 = atan((1 - _F) * tan(phi1))
    u2 = atan((1 - _F) * tan(phi2))
    sin_u1, cos_u1 = sin(u1), cos(u1)
    sin_u2, cos_u2 = sin(u2), cos(u2)

    lambda_ = delta_lon
    for _ in range(200):
        sin_lambda, cos_lambda = sin(lambda_), cos(lambda_)
        sin_sigma = sqrt(
            (cos_u2 * sin_lambda) ** 2
            + (cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lambda) ** 2
        )
        if sin_sigma == 0:
            return 0.0  # coincident points
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lambda
        sigma = atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lambda / sin_sigma
        cos_sq_alpha = 1 - sin_alpha**2
        if cos_sq_alpha == 0:
            cos_2sigma_m = 0.0  # equatorial line
        else:
            cos_2sigma_m = cos_sigma - 2 * sin_u1 * sin_u2 / cos_sq_alpha
        c = _F / 16 * cos_sq_alpha * (4 + _F * (4 - 3 * cos_sq_alpha))
        lambda_prev = lambda_
        lambda_ = delta_lon + (1 - c) * _F * sin_alpha * (
            sigma
            + c
            * sin_sigma
            * (cos_2sigma_m + c * cos_sigma * (-1 + 2 * cos_2sigma_m**2))
        )
        if abs(lambda_ - lambda_prev) < 1e-12:
            break
    else:
        return _haversine_m(lat1, lon1, lat2, lon2)

    u_sq = cos_sq_alpha * (_A**2 - _B**2) / _B**2
    a_ = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    b_ = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta_sigma = (
        b_
        * sin_sigma
        * (
            cos_2sigma_m
            + b_
            / 4
            * (
                cos_sigma * (-1 + 2 * cos_2sigma_m**2)
                - b_
                / 6
                * cos_2sigma_m
                * (-3 + 4 * sin_sigma**2)
                * (-3 + 4 * cos_2sigma_m**2)
            )
        )
    )
    return _B * a_ * (sigma - delta_sigma)


def _haversine_m(lat1, lon1, lat2, lon2):
    """Spherical fallback, mean-earth radius."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    h = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * 6371008.8 * atan2(sqrt(h), sqrt(1 - h))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing in degrees, 0 = north, clockwise."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_lambda = radians(lon2 - lon1)
    y = sin(d_lambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(d_lambda)
    return degrees(atan2(y, x)) % 360.0


def path_lengths(points):
    """Length in metres of each segment between consecutive points."""
    return [
        geodesic_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(points, points[1:])
    ]
