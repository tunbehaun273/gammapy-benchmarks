from pathlib import Path

import numpy as np
import astropy.units as u
import time
import yaml
from astropy.coordinates import SkyCoord, Angle
from regions import CircleSkyRegion
from gammapy.maps import Map, MapAxis
from gammapy.modeling import Fit
from gammapy.data import DataStore
from gammapy.modeling.models import PowerLawSpectralModel, PointSpatialModel, SkyModel
from gammapy.makers import (
    SpectrumDatasetMaker,
    ReflectedRegionsBackgroundMaker,
    SafeMaskMaker,
)
from gammapy.datasets import SpectrumDatasetOnOff, Datasets, SpectrumDataset
from gammapy.estimators import FluxPointsEstimator
import os

N_OBS = int(os.environ.get("GAMMAPY_BENCH_N_OBS", 10))


def data_prep():
    data_store = DataStore.from_dir("$GAMMAPY_DATA/hess-dl3-dr1/")
    OBS_ID = 23523
    obs_ids = OBS_ID * np.ones(N_OBS)
    observations = data_store.get_observations(obs_ids)

    target_position = SkyCoord(ra=83.63, dec=22.01, unit="deg", frame="icrs")
    on_region_radius = Angle("0.11 deg")
    on_region = CircleSkyRegion(center=target_position, radius=on_region_radius)

    exclusion_region = CircleSkyRegion(
        center=SkyCoord(183.604, -8.708, unit="deg", frame="galactic"),
        radius=0.5 * u.deg,
    )

    skydir = target_position.galactic
    exclusion_mask = Map.create(
        npix=(150, 150), binsz=0.05, skydir=skydir, proj="TAN", frame="galactic"
    )

    mask = exclusion_mask.geom.region_mask([exclusion_region], inside=False)
    exclusion_mask.data = mask

    e_reco = MapAxis.from_bounds(0.1, 40, nbin=40, interp="log", unit="TeV").edges
    e_true = MapAxis.from_bounds(0.05, 100, nbin=200, interp="log", unit="TeV").edges

    stacked = SpectrumDatasetOnOff.create(
        region=on_region, e_reco=e_reco, e_true=e_true, name="stacked"
    )

    dataset_maker = SpectrumDatasetMaker(
        containment_correction=False, selection=["counts", "aeff", "edisp"]
    )

    bkg_maker = ReflectedRegionsBackgroundMaker(exclusion_mask=exclusion_mask)
    safe_mask_masker = SafeMaskMaker(methods=["aeff-max"], aeff_percent=10)

    spectral_model = PowerLawSpectralModel(
        index=2, amplitude=2e-11 * u.Unit("cm-2 s-1 TeV-1"), reference=1 * u.TeV
    )
    spatial_model = PointSpatialModel(
        lon_0=target_position.ra, lat_0=target_position.dec, frame="icrs"
    )
    spatial_model.lon_0.frozen = True
    spatial_model.lat_0.frozen = True

    sky_model = SkyModel(
        spatial_model=spatial_model, spectral_model=spectral_model, name=""
    )

    for observation in observations:
        dataset = stacked.copy(name=f"dataset-{observation.obs_id}")
        dataset = dataset_maker.run(dataset=dataset, observation=observation)
        dataset_on_off = bkg_maker.run(dataset, observation)
        dataset_on_off = safe_mask_masker.run(dataset_on_off, observation)
        stacked.stack(dataset_on_off)

    stacked.models = sky_model
    return Datasets([stacked])


def write(stacked, filename):
    stacked.write(path=os.getcwd(), prefix=filename)


def read(filename):
    return Datasets.read(f"{filename}_datasets.yaml", f"{filename}_models.yaml")


def data_fit(stacked):
    # Data fitting
    fit = Fit(stacked)
    result = fit.run(optimize_opts={"print_level": 1})


def flux_point(stacked):
    e_edges = MapAxis.from_bounds(0.7, 30, nbin=11, interp="log", unit="TeV").edges
    fpe = FluxPointsEstimator(datasets=stacked, e_edges=e_edges)
    fpe.run()


def run_benchmark():
    info = {"n_obs": N_OBS}
    filename = "stacked"

    t = time.time()

    stacked = data_prep()
    info["data_preparation"] = time.time() - t
    t = time.time()

    write(stacked, filename)
    info["writing"] = time.time() - t
    t = time.time()

    stacked = read(filename)
    info["reading"] = time.time() - t
    t = time.time()

    data_fit(stacked)
    info["data_fitting"] = time.time() - t
    t = time.time()

    flux_point(stacked)
    info["flux_point"] = time.time() - t

    Path("bench.yaml").write_text(yaml.dump(info, sort_keys=False, indent=4))


if __name__ == "__main__":
    run_benchmark()
