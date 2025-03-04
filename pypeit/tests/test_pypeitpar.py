"""
Module to run tests on PypeItPar classes
"""
import os

import pytest

from pypeit.par import pypeitpar
from pypeit.par.util import parse_pypeit_file
from pypeit.spectrographs.util import load_spectrograph

def data_path(filename):
    data_dir = os.path.join(os.path.dirname(__file__), 'files')
    return os.path.join(data_dir, filename)

def test_framegroup():
    pypeitpar.FrameGroupPar()

def test_framegroup_types():
    t = pypeitpar.FrameGroupPar.valid_frame_types()
    assert 'bias' in t, 'Expected to find \'bias\' in list of valid frame types'

def test_processimages():
    pypeitpar.ProcessImagesPar()

def test_flatfield():
    pypeitpar.FlatFieldPar()

def test_flexure():
    pypeitpar.FlexurePar()

def test_coadd1d():
    pypeitpar.Coadd1DPar()

def test_coadd2d():
    pypeitpar.Coadd2DPar()

def test_cube():
    pypeitpar.CubePar()

def test_fluxcalibrate():
    pypeitpar.FluxCalibratePar()

def test_sensfunc():
    pypeitpar.SensFuncPar()

def test_sensfuncuvis():
    pypeitpar.SensfuncUVISPar()

def test_telluric():
    pypeitpar.TelluricPar()

def test_manualextraction():
    pypeitpar.ManualExtractionPar()

# TODO: Valid spectrographs are not longer read by pypeit.pypeitpar; it causes
# a circular import.
#def test_spectrographs():
#    s = pypeitpar.ReduxPar.valid_spectrographs()
#    assert 'keck_lris_blue' in s, 'Expected to find keck_lris_blue as a spectrograph!'

def test_redux():
    pypeitpar.ReduxPar()

def test_wavelengthsolution():
    pypeitpar.WavelengthSolutionPar()

def test_edgetrace():
    pypeitpar.EdgeTracePar()

def test_wavetilts():
    pypeitpar.WaveTiltsPar()

def test_reduce():
    pypeitpar.ReducePar()

def test_findobj():
    pypeitpar.FindObjPar()

def test_skysub():
    pypeitpar.SkySubPar()

def test_extraction():
    pypeitpar.ExtractionPar()

def test_calibrations():
    pypeitpar.CalibrationsPar()

def test_pypeit():
    pypeitpar.PypeItPar()

def test_fromcfgfile():
    pypeitpar.PypeItPar.from_cfg_file()

def test_writecfg():
    default_file = 'default.cfg'
    if os.path.isfile(default_file):
        os.remove(default_file)
    pypeitpar.PypeItPar.from_cfg_file().to_config(cfg_file=default_file)
    assert os.path.isfile(default_file), 'No file written!'
    os.remove(default_file)

def test_readcfg():
    default_file = 'default.cfg'
    if not os.path.isfile(default_file):
        pypeitpar.PypeItPar.from_cfg_file().to_config(cfg_file=default_file)
    pypeitpar.PypeItPar.from_cfg_file('default.cfg')
    os.remove(default_file)

def test_mergecfg():
    # Create a file with the defaults
    user_file = 'user_adjust.cfg'
    if os.path.isfile(user_file):
        os.remove(user_file)
    p = pypeitpar.PypeItPar.from_cfg_file()

    # Make some modifications
    p['rdx']['spectrograph'] = 'keck_lris_blue'
    p['calibrations']['biasframe']['useframe'] = 'overscan'

    # Write the modified config
    p.to_config(cfg_file=user_file)
    assert os.path.isfile(user_file), 'User file was not written!'

    # Read it in as a config to merge with the defaults
    p = pypeitpar.PypeItPar.from_cfg_file(merge_with=user_file)

    # Check the values are correctly read in
    assert p['rdx']['spectrograph'] == 'keck_lris_blue', 'Test spectrograph is incorrect!'
    assert p['calibrations']['biasframe']['useframe'] == 'overscan', \
                'Test biasframe:useframe is incorrect!'

    # Clean-up
    os.remove(user_file)

def test_sync():
    p = pypeitpar.PypeItPar()
    proc = pypeitpar.ProcessImagesPar()
    proc['combine'] = 'median'
#    proc['cr_sigrej'] = 20.5
    p.sync_processing(proc)
    assert p['scienceframe']['process']['combine'] == 'median'
    assert p['calibrations']['biasframe']['process']['combine'] == 'median'
    # Sigma rejection of cosmic rays for arc frames is already turned
    # off by default
#    assert p['calibrations']['arcframe']['process']['cr_sigrej'] < 0
#    assert p['calibrations']['traceframe']['process']['cr_sigrej'] == 20.5

def test_pypeit_file():
    # Read the PypeIt file
    cfg, data, frametype, usrdata, setups \
            = parse_pypeit_file(data_path('example_pypeit_file.pypeit'), file_check=False)
    # Long-winded way of getting the spectrograph name
    name = pypeitpar.PypeItPar.from_cfg_lines(merge_with=cfg)['rdx']['spectrograph']
    # Instantiate the spectrograph
    spectrograph = load_spectrograph(name)
    # Get the spectrograph specific configuration
    spec_cfg = spectrograph.default_pypeit_par().to_config()
    # Initialize the PypeIt parameters merge in the user config
    _p = pypeitpar.PypeItPar.from_cfg_lines(cfg_lines=spec_cfg, merge_with=cfg)
    # Test everything was merged correctly
    # This is a PypeItPar default that's not changed
    #assert p['calibrations']['pinholeframe']['number'] == 0
    # These are spectrograph specific defaults
    assert _p['fluxcalib'] is not None
    # These are user-level changes
    assert _p['calibrations']['traceframe']['process']['combine'] == 'median'
    assert _p['scienceframe']['process']['n_lohi'] == [8, 8]
    assert _p['reduce']['extraction']['manual'] is not None  # Set this to what it should be eventually

def test_telescope():
    pypeitpar.TelescopePar()

def test_fail_badpar():
    p = load_spectrograph('gemini_gnirs').default_pypeit_par()

    # Faults because there's no junk parameter
    cfg_lines = ['[calibrations]', '[[biasframe]]', '[[[process]]]', 'junk = True']
    with pytest.raises(ValueError):
        _p = pypeitpar.PypeItPar.from_cfg_lines(cfg_lines=p.to_config(), merge_with=cfg_lines)
    
def test_fail_badlevel():
    p = load_spectrograph('gemini_gnirs').default_pypeit_par()

    # Faults because process isn't at the right level (i.e., there's no
    # process parameter for CalibrationsPar)
    cfg_lines = ['[calibrations]', '[[biasframe]]', '[[process]]', 'cr_reject = True']
    with pytest.raises(ValueError):
        _p = pypeitpar.PypeItPar.from_cfg_lines(cfg_lines=p.to_config(), merge_with=cfg_lines)


