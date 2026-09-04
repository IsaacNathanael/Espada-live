import numpy as np

from espada.geo import haversine_km, local_xy_m, lonlat_from_local_m


def test_local_coordinate_roundtrip() -> None:
    source_lon = np.array([71.40, 71.45, 71.50])
    source_lat = np.array([18.65, 18.72, 18.80])
    x, y = local_xy_m(source_lon, source_lat, 71.45, 18.72)
    result_lon, result_lat = lonlat_from_local_m(x, y, 71.45, 18.72)
    np.testing.assert_allclose(result_lon, source_lon, atol=1e-12)
    np.testing.assert_allclose(result_lat, source_lat, atol=1e-12)


def test_haversine_zero_distance() -> None:
    assert haversine_km(71.45, 18.7167, 71.45, 18.7167) == 0.0

