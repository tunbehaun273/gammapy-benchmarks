import logging
import yaml
from pathlib import Path
import astropy.units as u
from astropy.coordinates import SkyCoord, Angle
from regions import CircleSkyRegion
from gammapy.analysis import Analysis, AnalysisConfig
from gammapy.maps import MapAxis
from gammapy.modeling import Fit
from gammapy.data import DataStore
from gammapy.modeling import Model
from gammapy.modeling.models import PowerLawSpectralModel, SkyModel
from gammapy.cube import SafeMaskMaker
from gammapy.spectrum import (
    SpectrumDatasetMaker,
    FluxPointsEstimator,
    ReflectedRegionsBackgroundMaker,
)

log = logging.getLogger(__name__)

with open("targets.yaml", "r") as stream:
    targets = yaml.safe_load(stream)

# If DEBUG is True, analyzes only 1 run (in the 1D joint analysis), complutes only 1 flux point and does
# not re-optimize the bkg during flux points computation
DEBUG = True

E_RECO = MapAxis.from_bounds(
    0.1, 100, nbin=72, unit="TeV", name="energy", interp="log"
).edges
NBIN = 24 if DEBUG is False else 1
FLUXP_EDGES = MapAxis.from_bounds(
    0.1, 100, nbin=NBIN, unit="TeV", name="energy", interp="log"
).edges


def main(analyse1d=True, analyse3d=True):
    # TODO: add the rxj1713 validation
    sources = ["crab", "msh1552", "pks2155"]
    for source in sources:
        # read config for the target
        target_filter = filter(lambda _: _["tag"] == source, targets)
        target_dict = list(target_filter)[0]

        log.info(f"Processing source: {source}")
        if analyse1d:
            run_analysis_1d(target_dict)
        if analyse3d:
            run_analysis_3d(target_dict)


def write_fit_summary(parameters, outfile):
    """Store fit results with uncertainties"""
    fit_results_dict = {}
    for parameter in parameters:
        value = parameter.value
        error = parameters.error(parameter)
        unit = parameter.unit
        name = parameter.name
        string = "{0:.2e} +- {1:.2e} {2}".format(value, error, unit)
        fit_results_dict.update({name: string})
    with open(str(outfile), "w") as f:
        yaml.dump(fit_results_dict, f)


def run_analysis_1d(target_dict):
    """Run joint spectral analysis for the selected target"""
    tag = target_dict["tag"]
    name = target_dict["name"]

    log.info(f"Running 1d analysis, {tag}")
    path_res = Path(tag + "/results/")

    ra = target_dict["ra"]
    dec = target_dict["dec"]
    on_size = target_dict["on_size"]
    e_decorr = target_dict["e_decorr"]

    target_pos = SkyCoord(ra, dec, unit="deg", frame="icrs")
    on_radius = Angle(on_size * u.deg)
    containment_corr = True

    log.info(f"Running observations selection")
    data_store = DataStore.from_dir("$GAMMAPY_DATA/hess-dl3-dr1/")
    mask = data_store.obs_table["TARGET_NAME"] == name
    obs_table = data_store.obs_table[mask]
    observations = data_store.get_observations(obs_table["OBS_ID"])

    if DEBUG is True:
        observations = [observations[0]]
    log.info(f"Running data reduction")
    # Reflected regions background estimation
    on_region = CircleSkyRegion(center=target_pos, radius=on_radius)
    dataset_maker = SpectrumDatasetMaker(
        region=on_region,
        e_reco=E_RECO,
        e_true=E_RECO,
        containment_correction=containment_corr,
    )
    bkg_maker = ReflectedRegionsBackgroundMaker()
    safe_mask_masker = SafeMaskMaker(methods=["edisp-bias"], bias_percent=10)

    datasets = []

    for observation in observations:
        dataset = dataset_maker.run(observation, selection=["counts", "aeff", "edisp"])
        dataset_on_off = bkg_maker.run(dataset, observation)
        dataset_on_off = safe_mask_masker.run(dataset_on_off, observation)
        datasets.append(dataset_on_off)

    log.info(f"Running fit ...")
    model = PowerLawSpectralModel(
        index=2, amplitude=2e-11 * u.Unit("cm-2 s-1 TeV-1"), reference=e_decorr * u.TeV
    )
    for dataset in datasets:
        dataset.model = model

    fit_joint = Fit(datasets)
    result_joint = fit_joint.run()
    parameters = model.parameters
    parameters.covariance = result_joint.parameters.covariance
    log.info(f"Writing {path_res}")
    write_fit_summary(parameters, str(path_res / "results-summary-fit-1d.yaml"))

    log.info(f"Running flux points estimation")
    fpe = FluxPointsEstimator(datasets=datasets, e_edges=FLUXP_EDGES)
    flux_points = fpe.run()
    flux_points.table["is_ul"] = flux_points.table["ts"] < 4
    keys = [
        "e_ref",
        "e_min",
        "e_max",
        "dnde",
        "dnde_errp",
        "dnde_errn",
        "is_ul",
        "dnde_ul",
    ]
    log.info(f"Writing {path_res}")
    flux_points.table_formatted[keys].write(
        path_res / "flux-points-1d.ecsv", format="ascii.ecsv"
    )


def run_analysis_3d(target_dict):
    """Run stacked 3D analysis for the selected target.

    Notice that, for the sake of time saving, we run a stacked analysis, as opposed
     to the joint analysis that is performed in the reference paper.
    """
    tag = target_dict["tag"]
    log.info(f"running 3d analysis, {tag}")

    path_res = Path(tag + "/results/")

    txt = Path("config_template.yaml").read_text()
    txt = txt.format_map(target_dict)
    config = yaml.safe_load(txt)
    config = AnalysisConfig(config)

    log.info(f"Running observations selection")
    analysis = Analysis(config)
    analysis.get_observations()

    log.info(f"Running data reduction")
    analysis.get_datasets()

    dataset = analysis.datasets[0]

    # TODO: Apply the safe energy threshold run-by-run
    # See reference paper, section 5.1.1.
    # 1) energy threshold given by the 10% edisp criterium
    e_thr_bias = dataset.edisp.get_bias_energy(0.1)

    # 2) energy at which the background peaks
    background_model = dataset.background_model
    bkg_spectrum = background_model.map.get_spectrum()
    peak = bkg_spectrum.data.max()
    idx = list(bkg_spectrum.data).index(peak)
    e_thr_bkg = bkg_spectrum.energy.center[idx]

    esafe = max(e_thr_bias, e_thr_bkg)
    dataset.mask_fit = dataset.counts.geom.energy_mask(emin=esafe)

    log.info(f"Running fit ...")
    ra = target_dict["ra"]
    dec = target_dict["dec"]
    e_decorr = target_dict["e_decorr"]
    spectral_model = Model.create("PowerLawSpectralModel", reference=e_decorr * u.TeV)
    spatial_model = Model.create(
        target_dict["spatial_model"], lon_0=f"{ra} deg", lat_0=f"{dec} deg"
    )
    if target_dict["spatial_model"] == "DiskSpatialModel":
        spatial_model.e.frozen = False
    sky_model = SkyModel(
        spatial_model=spatial_model, spectral_model=spectral_model, name=tag
    )

    # TODO: Get rid of this workaround, as soon as it's possible to set a SkyModel on analysis
    model = {}
    model["components"] = []
    model["components"].append(sky_model.to_dict())
    analysis.set_model(model=model)

    dataset.background_model.norm.frozen = False
    analysis.run_fit()

    parameters = analysis.model.parameters
    model_npars = len(sky_model.parameters.names)
    parameters.covariance = analysis.fit_result.parameters.covariance[
        0:model_npars, 0:model_npars
    ]
    log.info(f"Writing {path_res}")
    write_fit_summary(parameters, str(path_res / "results-summary-fit-3d.yaml"))

    log.info("Running flux points estimation")
    # TODO: This is a workaround to re-optimize the bkg. Remove it once it's added to the Analysis class
    for par in dataset.parameters:
        if par is not dataset.background_model.norm:
            par.frozen = True

    reoptimize = True if DEBUG is False else False
    fpe = FluxPointsEstimator(
        datasets=[dataset], e_edges=FLUXP_EDGES, source=tag, reoptimize=reoptimize
    )

    flux_points = fpe.run()
    flux_points.table["is_ul"] = flux_points.table["ts"] < 4
    keys = [
        "e_ref",
        "e_min",
        "e_max",
        "dnde",
        "dnde_errp",
        "dnde_errn",
        "is_ul",
        "dnde_ul",
    ]
    log.info(f"Writing {path_res}")
    flux_points.table_formatted[keys].write(
        path_res / "flux-points-3d.ecsv", format="ascii.ecsv"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
