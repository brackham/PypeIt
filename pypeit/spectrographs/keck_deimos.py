"""
Implements DEIMOS-specific functions, including reading in slitmask design
files.

.. include:: ../include/links.rst
"""
import os
import glob
import re
import warnings
from pkg_resources import resource_filename

from IPython import embed

import numpy as np

from scipy import interpolate

from astropy.io import fits
from astropy.coordinates import SkyCoord, Angle
from astropy.table import Table
from astropy import units, time

import linetools

from pypeit import msgs
from pypeit import telescopes
from pypeit import io
from pypeit.core import parse
from pypeit.core import framematch
from pypeit.core import wave
from pypeit import specobj, specobjs
from pypeit.spectrographs import spectrograph
from pypeit.images import detector_container

from pypeit.utils import index_of_x_eq_y

from pypeit.spectrographs.slitmask import SlitMask
from pypeit.spectrographs.opticalmodel import ReflectionGrating, OpticalModel, DetectorMap

class KeckDEIMOSSpectrograph(spectrograph.Spectrograph):
    """
    Child to handle Keck/DEIMOS specific code
    """
    ndet = 8
    name = 'keck_deimos'
    telescope = telescopes.KeckTelescopePar()
    camera = 'DEIMOS'
    header_name = 'DEIMOS'
    supported = True
    comment = 'Supported gratings: 600ZD, 830G, 900ZD, 1200B, 1200G; see :doc:`deimos`'

    def __init__(self):
        super().__init__()

        # These are specific to DEIMOS and are *not* defined by the base class.
        # Don't instantiate these until they're needed.
        self.grating = None
        self.optical_model = None
        self.detector_map = None
        self.amap = None
        self.bmap = None

    def get_detector_par(self, det, hdu=None):
        """
        Return metadata for the selected detector.

        Args:
            det (:obj:`int`):
                1-indexed detector number.
            hdu (`astropy.io.fits.HDUList`_, optional):
                The open fits file with the raw image of interest.  If not
                provided, frame-dependent parameters are set to a default.

        Returns:
            :class:`~pypeit.images.detector_container.DetectorContainer`:
            Object with the detector metadata.
        """
        # Binning
        # TODO: Could this be detector dependent?
        binning = '1,1' if hdu is None else self.get_meta_value(self.get_headarr(hdu), 'binning')

        # Detector 1
        detector_dict1 = dict(
            binning         = binning,
            det             = 1,
            dataext         = 1,
            specaxis        = 0,
            specflip        = False,
            spatflip        = False,
            platescale      = 0.1185,
            darkcurr        = 4.19,
            saturation      = 65535., # ADU
            nonlinear       = 0.95,   # Changed by JFH from 0.86 to 0.95
            mincounts       = -1e10,
            numamplifiers   = 1,
            gain            = np.atleast_1d(1.226),
            ronoise         = np.atleast_1d(2.570),
            )
        # Detector 2
        detector_dict2 = detector_dict1.copy()
        detector_dict2.update(dict(
            det=2,
            dataext=2,
            darkcurr=3.46,
            gain=np.atleast_1d(1.188),
            ronoise=np.atleast_1d(2.491),
        ))
        # Detector 3
        detector_dict3 = detector_dict1.copy()
        detector_dict3.update(dict(
            det=3,
            dataext=3,
            darkcurr=4.03,
            gain=np.atleast_1d(1.248),
            ronoise=np.atleast_1d(2.618),
        ))
        # Detector 4
        detector_dict4 = detector_dict1.copy()
        detector_dict4.update(dict(
            det=4,
            dataext=4,
            darkcurr=3.80,
            gain=np.atleast_1d(1.220),
            ronoise=np.atleast_1d(2.557),
        ))
        # Detector 5
        detector_dict5 = detector_dict1.copy()
        detector_dict5.update(dict(
            det=5,
            dataext=5,
            darkcurr=4.71,
            gain=np.atleast_1d(1.184),
            ronoise=np.atleast_1d(2.482),
        ))
        # Detector 6
        detector_dict6 = detector_dict1.copy()
        detector_dict6.update(dict(
            det=6,
            dataext=6,
            darkcurr=4.28,
            gain=np.atleast_1d(1.177),
            ronoise=np.atleast_1d(2.469),
        ))
        # Detector 7
        detector_dict7 = detector_dict1.copy()
        detector_dict7.update(dict(
            det=7,
            dataext=7,
            darkcurr=3.33,
            gain=np.atleast_1d(1.201),
            ronoise=np.atleast_1d(2.518),
        ))
        # Detector 8
        detector_dict8 = detector_dict1.copy()
        detector_dict8.update(dict(
            det=8,
            dataext=8,
            darkcurr=3.69,
            gain=np.atleast_1d(1.230),
            ronoise=np.atleast_1d(2.580),
        ))
        detectors = [detector_dict1, detector_dict2, detector_dict3, detector_dict4,
                     detector_dict5, detector_dict6, detector_dict7, detector_dict8]
        # Return
        return detector_container.DetectorContainer(**detectors[det-1])

    @classmethod
    def default_pypeit_par(cls):
        """
        Return the default parameters to use for this instrument.
        
        Returns:
            :class:`~pypeit.par.pypeitpar.PypeItPar`: Parameters required by
            all of ``PypeIt`` methods.
        """
        par = super().default_pypeit_par()

        # Spectral flexure correction
        par['flexure']['spec_method'] = 'boxcar'
        # Set wave tilts order
        par['calibrations']['slitedges']['edge_thresh'] = 50.
        par['calibrations']['slitedges']['fit_order'] = 3
        par['calibrations']['slitedges']['minimum_slit_gap'] = 0.25
        par['calibrations']['slitedges']['minimum_slit_length_sci'] = 4.

        # 1D wavelength solution
        par['calibrations']['wavelengths']['lamps'] = ['ArI','NeI','KrI','XeI']
        par['calibrations']['wavelengths']['n_first'] = 3
        par['calibrations']['wavelengths']['match_toler'] = 2.5

        # Do not require bias frames
        turn_off = dict(use_biasimage=False)
        par.reset_all_processimages_par(**turn_off)

        # Alter the method used to combine pixel flats
        par['calibrations']['pixelflatframe']['process']['combine'] = 'median'
        par['calibrations']['pixelflatframe']['process']['comb_sigrej'] = 10.

        # Do not sigmaclip the arc frames
        par['calibrations']['arcframe']['process']['clip'] = False
        # Do not sigmaclip the tilt frames
        par['calibrations']['tiltframe']['process']['clip'] = False
        # Lower value of tracethresh
        par['calibrations']['tilts']['tracethresh'] = 10

        # LACosmics parameters
        par['scienceframe']['process']['sigclip'] = 4.0
        par['scienceframe']['process']['objlim'] = 1.5

        # Find objects
        #  The following corresponds to 1.1" if unbinned (DEIMOS is never binned)
        par['reduce']['findobj']['find_fwhm'] = 10.  

        # If telluric is triggered
        par['sensfunc']['IR']['telgridfile'] \
                = os.path.join(par['sensfunc']['IR'].default_root,
                               'TelFit_MaunaKea_3100_26100_R20000.fits')
        return par

    def config_specific_par(self, scifile, inp_par=None):
        """
        Modify the ``PypeIt`` parameters to hard-wired values used for
        specific instrument configurations.

        Args:
            scifile (:obj:`str`):
                File to use when determining the configuration and how
                to adjust the input parameters.
            inp_par (:class:`~pypeit.par.parset.ParSet`, optional):
                Parameter set used for the full run of PypeIt.  If None,
                use :func:`default_pypeit_par`.

        Returns:
            :class:`~pypeit.par.parset.ParSet`: The PypeIt parameter set
            adjusted for configuration specific parameter values.
        """
        par = super().config_specific_par(scifile, inp_par=inp_par)

        headarr = self.get_headarr(scifile)

        # When using LVM mask reduce only detectors 3,7
        if 'LVMslit' in self.get_meta_value(headarr, 'decker'):
            par['rdx']['detnum'] = [3,7]

        # Turn PCA off for long slits
        # TODO: I'm a bit worried that this won't catch all
        # long-slits...
        if ('Long' in self.get_meta_value(headarr, 'decker')) or (
                'LVMslit' in self.get_meta_value(headarr, 'decker')):
            par['calibrations']['slitedges']['sync_predict'] = 'nearest'

        # Turn on the use of mask design
        if ('Long' not in self.get_meta_value(headarr, 'decker')) and (
                'LVMslit' not in self.get_meta_value(headarr, 'decker')):
            # TODO -- Move this parameter into SlitMaskPar??
            par['calibrations']['slitedges']['use_maskdesign'] = True
            # Since we use the slitmask info to find the alignment boxes, I don't need `minimum_slit_length_sci`
            par['calibrations']['slitedges']['minimum_slit_length_sci'] = None
            # Sometime the added missing slits at the edge of the detector are to small to be useful.
            par['calibrations']['slitedges']['minimum_slit_length'] = 3.
            # Since we use the slitmask info to add and remove traces, 'minimum_slit_gap' may undo the matching effort.
            par['calibrations']['slitedges']['minimum_slit_gap'] = 0.
            # Lower edge_thresh works better
            par['calibrations']['slitedges']['edge_thresh'] = 10.
            # needed for better slitmask design matching
            par['calibrations']['flatfield']['tweak_slits'] = False
            # Assign RA, DEC, OBJNAME to detected objects
            par['reduce']['slitmask']['assign_obj'] = True
            # force extraction of undetected objects
            par['reduce']['slitmask']['extract_missing_objs'] = True

        # Templates
        if self.get_meta_value(headarr, 'dispname') == '600ZD':
            par['calibrations']['wavelengths']['method'] = 'full_template'
            par['calibrations']['wavelengths']['reid_arxiv'] = 'keck_deimos_600ZD.fits'
            # par['calibrations']['wavelengths']['lamps'] += ['CdI', 'ZnI', 'HgI']
        elif self.get_meta_value(headarr, 'dispname') == '830G':
            par['calibrations']['wavelengths']['method'] = 'full_template'
            par['calibrations']['wavelengths']['reid_arxiv'] = 'keck_deimos_830G.fits'
        elif self.get_meta_value(headarr, 'dispname') == '1200G':
            par['calibrations']['wavelengths']['method'] = 'full_template'
            par['calibrations']['wavelengths']['reid_arxiv'] = 'keck_deimos_1200G.fits'
        elif self.get_meta_value(headarr, 'dispname') == '1200B':
            par['calibrations']['wavelengths']['method'] = 'full_template'
            par['calibrations']['wavelengths']['reid_arxiv'] = 'keck_deimos_1200B.fits'
            # par['calibrations']['wavelengths']['lamps'] += ['CdI', 'ZnI', 'HgI']
        elif self.get_meta_value(headarr, 'dispname') == '900ZD':
            par['calibrations']['wavelengths']['method'] = 'full_template'
            par['calibrations']['wavelengths']['reid_arxiv'] = 'keck_deimos_900ZD.fits'
            # par['calibrations']['wavelengths']['lamps'] += ['CdI', 'ZnI', 'HgI']
        # Arc lamps list from header
        par['calibrations']['wavelengths']['lamps'] = ['use_header']

        # FWHM
        binning = parse.parse_binning(self.get_meta_value(headarr, 'binning'))
        par['calibrations']['wavelengths']['fwhm'] = 6.0 / binning[1]
        par['calibrations']['wavelengths']['fwhm_fromlines'] = True

        # Return
        return par

    def init_meta(self):
        """
        Define how metadata are derived from the spectrograph files.

        That is, this associates the ``PypeIt``-specific metadata keywords
        with the instrument-specific header cards using :attr:`meta`.
        """
        self.meta = {}
        # Required (core)
        self.meta['ra'] = dict(ext=0, card='RA')
        self.meta['dec'] = dict(ext=0, card='DEC')
        self.meta['target'] = dict(ext=0, card='TARGNAME')
        self.meta['decker'] = dict(ext=0, card='SLMSKNAM')
        self.meta['binning'] = dict(card=None, compound=True)

        self.meta['mjd'] = dict(card=None, compound=True)
        self.meta['exptime'] = dict(ext=0, card='ELAPTIME')
        self.meta['airmass'] = dict(ext=0, card='AIRMASS')
        self.meta['dispname'] = dict(ext=0, card='GRATENAM')
        # Extras for config and frametyping
        self.meta['hatch'] = dict(ext=0, card='HATCHPOS')
        self.meta['dispangle'] = dict(card=None, compound=True, rtol=1e-5)
        # Image type
        self.meta['idname'] = dict(ext=0, card='OBSTYPE')
        # Lamps
        self.meta['lampstat01'] = dict(ext=0, card='LAMPS')
        # Extras for pypeit file
        self.meta['dateobs'] = dict(ext=0, card='DATE-OBS')
        self.meta['utc'] = dict(ext=0, card='UTC')
        self.meta['mode'] = dict(ext=0, card='MOSMODE')
        self.meta['amp'] = dict(ext=0, card='AMPMODE')
        self.meta['object'] = dict(ext=0, card='OBJECT')
        self.meta['filter1'] = dict(ext=0, card='DWFILNAM')
        self.meta['frameno'] = dict(ext=0, card='FRAMENO')
        self.meta['instrument'] = dict(ext=0, card='INSTRUME')

    def compound_meta(self, headarr, meta_key):
        """
        Methods to generate metadata requiring interpretation of the header
        data, instead of simply reading the value of a header card.

        Args:
            headarr (:obj:`list`):
                List of `astropy.io.fits.Header`_ objects.
            meta_key (:obj:`str`):
                Metadata keyword to construct.

        Returns:
            object: Metadata value read from the header(s).
        """
        if meta_key == 'binning':
            binspatial, binspec = parse.parse_binning(headarr[0]['BINNING'])
            binning = parse.binning2string(binspec, binspatial)
            return binning
        elif meta_key == 'dispangle':
            if headarr[0]['GRATEPOS'] == 3:
                return headarr[0]['G3TLTWAV']
            elif headarr[0]['GRATEPOS'] == 4:
                return headarr[0]['G4TLTWAV']
            else:
                msgs.warn('This is probably a problem. Non-standard DEIMOS GRATEPOS={0}.'.format(headarr[0]['GRATEPOS']))
        elif meta_key == 'mjd':
            if headarr[0].get('MJD-OBS', None) is not None:
                return headarr[0]['MJD-OBS']
            else:
                return time.Time('{}T{}'.format(headarr[0]['DATE-OBS'], headarr[0]['UTC'])).mjd
        else:
            msgs.error("Not ready for this compound meta")

    def configuration_keys(self):
        """
        Return the metadata keys that define a unique instrument
        configuration.

        This list is used by :class:`~pypeit.metadata.PypeItMetaData` to
        identify the unique configurations among the list of frames read
        for a given reduction.

        Returns:
            :obj:`list`: List of keywords of data pulled from file headers
            and used to constuct the :class:`~pypeit.metadata.PypeItMetaData`
            object.
        """
        # TODO: Based on a conversation with Carlos, we might want to
        # include dateobs with this. For now, amp is effectively
        # redundant because anything with the wrong amplifier used is
        # removed from the list of valid frames in PypeItMetaData.
        return ['dispname', 'decker', 'binning', 'dispangle', 'amp', 'filter1']

    def valid_configuration_values(self):
        """
        Return a fixed set of valid values for any/all of the configuration
        keys.

        Returns:
            :obj:`dict`: A dictionary with any/all of the configuration keys
            and their associated discrete set of valid values. If there are
            no restrictions on configuration values, None is returned.
        """
        return {'amp': ['SINGLE:B'], 'mode':['Spectral']}

    def config_independent_frames(self):
        """
        Define frame types that are independent of the fully defined
        instrument configuration.

        Bias and dark frames are considered independent of a configuration,
        but the DATE-OBS keyword is used to assign each to the most-relevant
        configuration frame group. See
        :func:`~pypeit.metadata.PypeItMetaData.set_configurations`.

        Returns:
            :obj:`dict`: Dictionary where the keys are the frame types that
            are configuration independent and the values are the metadata
            keywords that can be used to assign the frames to a configuration
            group.
        """
        return {'bias': 'dateobs', 'dark': 'dateobs'}

    def pypeit_file_keys(self):
        """
        Define the list of keys to be output into a standard ``PypeIt`` file.

        Returns:
            :obj:`list`: The list of keywords in the relevant
            :class:`~pypeit.metadata.PypeItMetaData` instance to print to the
            :ref:`pypeit_file`.
        """
        return super().pypeit_file_keys() + ['dateobs', 'utc', 'frameno']

    def subheader_for_spec(self, row_fitstbl, raw_header, extra_header_cards=None,
                           allow_missing=False):
        """
        Generate a dict that will be added to the Header of spectra files
        generated by ``PypeIt`` (e.g. :class:`~pypeit.specobjs.SpecObjs`).
        This version overrides the parent version to include KOA specific header cards.

        Args:
            row_fitstbl (dict-like):
                Typically an `astropy.table.Row`_ or
                `astropy.io.fits.Header`_ with keys defined by
                :func:`~pypeit.core.meta.define_core_meta`.
            raw_header (`astropy.io.fits.Header`_):
                Header that defines the instrument and detector, meaning that
                the header must contain the ``INSTRUME`` and ``DETECTOR``
                header cards. If provided, this must also contain the header
                cards provided by ``extra_header_cards``.
            extra_header_cards (:obj:`list`, optional):
                Additional header cards from ``raw_header`` to include in the
                output dictionary. Can be an empty list or None.
            allow_missing (:obj:`bool`, optional):
                Ignore any keywords returned by
                :func:`~pypeit.core.meta.define_core_meta` are not present in
                ``row_fitstbl``. Otherwise, raise ``PypeItError``.

        Returns:
            :obj:`dict`: Dictionary with data to include an output fits
            header file or table downstream.
        """
        koa_header_cards = ['KOAID', 'PROGPI', "PROGID", "SEMESTER", 'GUIDFWHM']
        if extra_header_cards is not None:
            extra_header_cards += koa_header_cards
        else:
            extra_header_cards = koa_header_cards

        return super().subheader_for_spec(row_fitstbl, raw_header, extra_header_cards, allow_missing)

    def check_frame_type(self, ftype, fitstbl, exprng=None):
        """
        Check for frames of the provided type.

        Args:
            ftype (:obj:`str`):
                Type of frame to check. Must be a valid frame type; see
                frame-type :ref:`frame_type_defs`.
            fitstbl (`astropy.table.Table`_):
                The table with the metadata for one or more frames to check.
            exprng (:obj:`list`, optional):
                Range in the allowed exposure time for a frame of type
                ``ftype``. See
                :func:`pypeit.core.framematch.check_frame_exptime`.

        Returns:
            `numpy.ndarray`_: Boolean array with the flags selecting the
            exposures in ``fitstbl`` that are ``ftype`` type frames.
        """
        good_exp = (framematch.check_frame_exptime(fitstbl['exptime'], exprng)) \
                        & (fitstbl['mode'] == 'Spectral')
        if ftype == 'science':
            return good_exp & (fitstbl['idname'] == 'Object') & (fitstbl['lampstat01'] == 'Off') \
                        & (fitstbl['hatch'] == 'open')
        if ftype == 'bias':
            return good_exp & (fitstbl['idname'] == 'Bias') & (fitstbl['lampstat01'] == 'Off') \
                        & (fitstbl['hatch'] == 'closed')
        if ftype in ['pixelflat', 'trace', 'illumflat']:
            # Flats and trace frames are typed together
            is_flat = np.any(np.vstack([(fitstbl['idname'] == n) & (fitstbl['hatch'] == h)
                                    for n,h in zip(['IntFlat', 'DmFlat', 'SkyFlat'],
                                                   ['closed', 'open', 'open'])]), axis=0)
            return good_exp & is_flat & (fitstbl['lampstat01'] != 'Off')
        if ftype == 'pinhole':
            # Pinhole frames are never assigned for DEIMOS
            return np.zeros(len(fitstbl), dtype=bool)
        if ftype == 'dark':
            return good_exp & (fitstbl['idname'] == 'Dark') & (fitstbl['lampstat01'] == 'Off') \
                        & (fitstbl['hatch'] == 'closed')
        if ftype in ['arc', 'tilt']:
            return good_exp & (fitstbl['idname'] == 'Line') & (fitstbl['hatch'] == 'closed') \
                        & (fitstbl['lampstat01'] != 'Off')

        msgs.warn('Cannot determine if frames are of type {0}.'.format(ftype))
        return np.zeros(len(fitstbl), dtype=bool)

    # TODO: We should aim to get rid of this... I'm not sure it's ever used...
    def idname(self, ftype):
        """
        Return the ``idname`` for the selected frame type for this
        instrument.

        Args:
            ftype (:obj:`str`):
                Frame type, which should be one of the keys in
                :class:`~pypeit.core.framematch.FrameTypeBitMask`.

        Returns:
            :obj:`str`: The value of ``idname`` that should be available in
            the :class:`~pypeit.metadata.PypeItMetaData` instance that
            identifies frames of this type.
        """
        # TODO: Fill in the rest of these.
        name = { 'arc': 'Line',
                 'tilt': None,
                 'bias': None,
                 'dark': None,
                 'pinhole': None,
                 'pixelflat': 'IntFlat',
                 'science': 'Object',
                 'standard': None,
                 'trace': 'IntFlat' }
        return name[ftype]

    def get_rawimage(self, raw_file, det):
        """
        Read raw images and generate a few other bits and pieces
        that are key for image processing.

        Data are unpacked from the multi-extension HDU.  Function is
        based on :func:`pypeit.spectrographs.keck_lris.read_lris`, which
        was based on the IDL procedure ``readmhdufits.pro``.

        .. warning::

            ``PypeIt`` currently *cannot* reduce images produced by
            reading the DEIMOS CCDs with the A amplifier or those
            taken in imaging mode. All image handling assumes DEIMOS
            images have been read with the B amplifier in the
            "Spectral" observing mode. This method will fault if this
            is not true based on the header keywords MOSMODE and
            AMPMODE.

        Parameters
        ----------
        raw_file : :obj:`str`
            File to read
        det : :obj:`int`
            1-indexed detector to read

        Returns
        -------
        detector_par : :class:`pypeit.images.detector_container.DetectorContainer`
            Detector metadata parameters.
        raw_img : `numpy.ndarray`_
            Raw image for this detector.
        hdu : `astropy.io.fits.HDUList`_
            Opened fits file
        exptime : :obj:`float`
            Exposure time read from the file header
        rawdatasec_img : `numpy.ndarray`_
            Data (Science) section of the detector as provided by setting the
            (1-indexed) number of the amplifier used to read each detector
            pixel. Pixels unassociated with any amplifier are set to 0.
        oscansec_img : `numpy.ndarray`_
            Overscan section of the detector as provided by setting the
            (1-indexed) number of the amplifier used to read each detector
            pixel. Pixels unassociated with any amplifier are set to 0.
        """
        # Check for file; allow for extra .gz, etc. suffix
        # TODO: Why not use os.path.isfile?
        fil = glob.glob(raw_file + '*')
        if len(fil) != 1:
            msgs.error('Found {0} files matching {1}'.format(len(fil), raw_file + '*'))
        # Read
        msgs.info("Reading DEIMOS file: {:s}".format(fil[0]))

        hdu = io.fits_open(fil[0])
        if hdu[0].header['AMPMODE'] != 'SINGLE:B':
            msgs.error('PypeIt can only reduce images with AMPMODE == SINGLE:B.')
        if hdu[0].header['MOSMODE'] != 'Spectral':
            msgs.error('PypeIt can only reduce images with MOSMODE == Spectral.')

        # Get post, pre-pix values
        postpix = hdu[0].header['POSTPIX']
        detlsize = hdu[0].header['DETLSIZE']
        x0, x_npix, y0, y_npix = np.array(parse.load_sections(detlsize)).flatten()

        # Create final image
        if det is None:
            image = np.zeros((x_npix, y_npix + 4 * postpix))
            rawdatasec_img = np.zeros_like(image, dtype=int)
            oscansec_img = np.zeros_like(image, dtype=int)

        # get the x and y binning factors...
        binning = hdu[0].header['BINNING']
        if binning != '1,1':
            msgs.error("This binning for DEIMOS might not work.  But it might..")

        # DEIMOS detectors
        nchip = 8

        if det is None:
            chips = range(nchip)
        else:
            chips = [det - 1]  # Indexing starts at 0 here
        # Loop
        for tt in chips:
            data, oscan = deimos_read_1chip(hdu, tt + 1)

            # One detector??
            if det is not None:
                image = np.zeros((data.shape[0], data.shape[1] + oscan.shape[1]))
                rawdatasec_img = np.zeros_like(image, dtype=int)
                oscansec_img = np.zeros_like(image, dtype=int)

            # Indexing
            x1, x2, y1, y2, o_x1, o_x2, o_y1, o_y2 = indexing(tt, postpix, det=det)

            # Fill
            image[y1:y2, x1:x2] = data
            rawdatasec_img[y1:y2, x1:x2] = 1 # Amp
            image[o_y1:o_y2, o_x1:o_x2] = oscan
            oscansec_img[o_y1:o_y2, o_x1:o_x2] = 1 # Amp

        # Return
        exptime = hdu[self.meta['exptime']['ext']].header[self.meta['exptime']['card']]
        return self.get_detector_par(det if det is not None else 1, hdu=hdu), \
               image, hdu, exptime, rawdatasec_img, oscansec_img

    def bpm(self, filename, det, shape=None, msbias=None):
        """
        Generate a default bad-pixel mask.

        Even though they are both optional, either the precise shape for
        the image (``shape``) or an example file that can be read to get
        the shape (``filename`` using :func:`get_image_shape`) *must* be
        provided.

        Args:
            filename (:obj:`str` or None):
                An example file to use to get the image shape.
            det (:obj:`int`):
                1-indexed detector number to use when getting the image
                shape from the example file.
            shape (tuple, optional):
                Processed image shape
                Required if filename is None
                Ignored if filename is not None
            msbias (`numpy.ndarray`_, optional):
                Master bias frame used to identify bad pixels

        Returns:
            `numpy.ndarray`_: An integer array with a masked value set
            to 1 and an unmasked value set to 0.  All values are set to
            0.
        """
        # Call the base-class method to generate the empty bpm
        bpm_img = super().bpm(filename, det, shape=shape, msbias=msbias)

        if det == 1:
            bpm_img[:,1052:1054] = 1
        elif det == 2:
            bpm_img[:,0:4] = 1
            bpm_img[:,376:381] = 1
            bpm_img[:,489] = 1
            bpm_img[:,1333:1335] = 1
            bpm_img[:,2047] = 1
        elif det == 3:
            bpm_img[:,0:4] = 1
            bpm_img[:,221] = 1
            bpm_img[:,260] = 1
            bpm_img[:,366] = 1
            bpm_img[:,816:819] = 1
            bpm_img[:,851] = 1
            bpm_img[:,940] = 1
            bpm_img[:,1167] = 1
            bpm_img[:,1280] = 1
            bpm_img[:,1301:1303] = 1
            bpm_img[:,1744:1747] = 1
            bpm_img[:,-4:] = 1
        elif det == 4:
            bpm_img[:,0:4] = 1
            bpm_img[:,47] = 1
            bpm_img[:,744] = 1
            bpm_img[:,790:792] = 1
            bpm_img[:,997:999] = 1
        elif det == 5:
            bpm_img[:,25:27] = 1
            bpm_img[:,128:130] = 1
            bpm_img[:,1535:1539] = 1
        elif det == 7:
            bpm_img[:,426:428] = 1
            bpm_img[:,676] = 1
            bpm_img[:,1176:1178] = 1
        elif det == 8:
            bpm_img[:,440] = 1
            bpm_img[:,509:513] = 1
            bpm_img[:,806] = 1
            bpm_img[:,931:934] = 1

        return bpm_img

    def get_lamps(self, fitstbl):
        """
        Extract the list of arc lamps used from header

        Args:
            fitstbl (`astropy.table.Table`_):
                The table with the metadata for one or more arc frames.

        Returns:
            lamps (:obj:`list`) : List used arc lamps

        """

        return [f'{lamp}I' for lamp in np.unique(np.concatenate([lname.split() for lname in fitstbl['lampstat01']]))]

    def get_telescope_offset(self, file_list):
        """
        For a list of frames compute telescope pointing offset w.r.t. the first frame.
        Note that the object in the slit will appear moving in the opposite direction (=-tel_off)

        Args:
            file_list (:obj:`list`): List of frames (including the path) for which telescope offset is desired.
            Both raw frames and spec2d files can be used.

        Returns:
            `numpy.ndarray`_: List of telescope offsets (in arcsec) w.r.t. the first frame

        """
        # file (can be a raw or a spec2d)
        deimos_files = np.atleast_1d(file_list)
        # headers for all the files
        hdrs = np.array([self.get_headarr(file) for file in deimos_files], dtype=object)
        # mjd for al the files
        mjds = np.array([self.get_meta_value(aa, 'mjd') for aa in hdrs], dtype=object)
        # sort
        sorted_by_mjd = np.argsort(mjds)
        # telescope coordinates
        # precision: RA=0.15", Dec=0.1"
        ras = np.array([self.get_meta_value(aa, 'ra') for aa in hdrs], dtype=object)[sorted_by_mjd]
        decs = np.array([self.get_meta_value(aa, 'dec') for aa in hdrs], dtype=object)[sorted_by_mjd]
        coords = SkyCoord(ra=ras, dec=decs, frame='fk5', unit='deg')

        # compute telescope offsets with respect to the first frame
        tel_off = []
        for i in range(len(coords)):
            offset = coords[0].separation(coords[i])
            pa = coords[0].position_angle(coords[i])
            # ROTPOSN take into account small changes in the mask PA
            maskpa = Angle((hdrs[i][0]['ROTPOSN'] + 90.) * units.deg)
            # tetha = PA in the slitmask reference frame
            theta = pa - maskpa
            # telescope offset
            tel_off.append(offset.arcsec * np.cos(theta))

        return np.array(tel_off)

    def get_slitmask(self, filename):
        """
        Parse the slitmask data from a DEIMOS file into :attr:`slitmask`, a
        :class:`~pypeit.spectrographs.slitmask.SlitMask` object.

        Args:
            filename (:obj:`str`):
                Name of the file to read.

        Returns:
            :class:`~pypeit.spectrographs.slitmask.SlitMask`: The slitmask
            data read from the file. The returned object is the same as
            :attr:`slitmask`.
        """
        # Open the file
        hdu = io.fits_open(filename)

        # Build the object data
        #   - Find the index of the object IDs in the slit-object
        #     mapping that match the object catalog
        mapid = hdu['SlitObjMap'].data['ObjectID']
        catid = hdu['ObjectCat'].data['ObjectID']
        indx = index_of_x_eq_y(mapid, catid)
        objname = [item.strip() for item in hdu['ObjectCat'].data['OBJECT']]
        #   - Pull out the slit ID, object ID, name, object coordinates, top and bottom distance
        objects = np.array([hdu['SlitObjMap'].data['dSlitId'][indx].astype(int),
                            catid.astype(int),
                            hdu['ObjectCat'].data['RA_OBJ'],
                            hdu['ObjectCat'].data['DEC_OBJ'],
                            objname,
                            hdu['ObjectCat'].data['mag'],
                            hdu['ObjectCat'].data['pBand'],
                            hdu['SlitObjMap'].data['TopDist'][indx],
                            hdu['SlitObjMap'].data['BotDist'][indx]]).T
        #   - Only keep the objects that are in the slit-object mapping
        objects = objects[mapid[indx] == catid]

        # Match the slit IDs in DesiSlits to those in BluSlits
        indx = index_of_x_eq_y(hdu['DesiSlits'].data['dSlitId'], hdu['BluSlits'].data['dSlitId'],
                               strict=True)

        # PA corresponding to positive x on detector (spatial)
        posx_pa = hdu['MaskDesign'].data['PA_PNT'][0]
        if posx_pa < 0.:
            posx_pa += 360.

        # Instantiate the slit mask object and return it
        self.slitmask = SlitMask(np.array([hdu['BluSlits'].data['slitX1'],
                                           hdu['BluSlits'].data['slitY1'],
                                           hdu['BluSlits'].data['slitX2'],
                                           hdu['BluSlits'].data['slitY2'],
                                           hdu['BluSlits'].data['slitX3'],
                                           hdu['BluSlits'].data['slitY3'],
                                           hdu['BluSlits'].data['slitX4'],
                                           hdu['BluSlits'].data['slitY4']]).T.reshape(-1,4,2),
                                 slitid=hdu['BluSlits'].data['dSlitId'],
                                 align=hdu['DesiSlits'].data['slitTyp'][indx] == 'A',
                                 science=hdu['DesiSlits'].data['slitTyp'][indx] == 'P',
                                 onsky=np.array([hdu['DesiSlits'].data['slitRA'][indx],
                                                 hdu['DesiSlits'].data['slitDec'][indx],
                                                 hdu['DesiSlits'].data['slitLen'][indx],
                                                 hdu['DesiSlits'].data['slitWid'][indx],
                                                 hdu['DesiSlits'].data['slitLPA'][indx]]).T,
                                 objects=objects,
                                 #object_names=hdu['ObjectCat'].data['OBJECT'],
                                 posx_pa=posx_pa)
        return self.slitmask

    # TODO: Allow this to accept the relevant row from the PypeItMetaData
    # object instead?
    def get_grating(self, filename):
        """
        Instantiate :attr:`grating` (a
        :class:`~pypeit.spectrographs.opticalmodel.ReflectionGrating`
        instance) based on the grating using to collect the data provided by
        the filename.

        Taken from xidl/DEEP2/spec2d/pro/deimos_omodel.pro and
        xidl/DEEP2/spec2d/pro/deimos_grating.pro

        Args:
            filename (:obj:`str`):
                Name of the file with the grating metadata.

        Returns:
            :class:`~pypeit.spectrographs.opticalmodel.ReflectionGrating`:
            The grating instance relevant to the data in ``filename``. The
            returned object is the same as :attr:`grating`.
        """
        hdu = io.fits_open(filename)

        # Grating slider
        slider = hdu[0].header['GRATEPOS']
        # TODO: Add test for slider

        # Central wavelength, grating angle, and tilt position
        if slider == 3:
            central_wave = hdu[0].header['G3TLTWAV']
            # Not used
            #angle = (hdu[0].header['G3TLTRAW'] + 29094)/2500
            tilt = hdu[0].header['G3TLTVAL']
        elif slider in [2,4]:
            # Slider is 2 or 4
            central_wave = hdu[0].header['G4TLTWAV']
            # Not used
            #angle = (hdu[0].header['G4TLTRAW'] + 40934)/2500
            tilt = hdu[0].header['G4TLTVAL']
        else:
            raise ValueError('Slider has unknown value: {0}'.format(slider))

        # Ruling
        name = hdu[0].header['GRATENAM']
        if 'Mirror' in name:
            ruling = 0
        else:
            # Remove all non-numeric characters from the name and
            # convert to a floating point number
            ruling = float(re.sub('[^0-9]', '', name))
            # Adjust
            if abs(ruling-1200) < 0.5:
                ruling = 1200.06
            elif abs(ruling-831) <  2:
                ruling = 831.90

        # Get the orientation of the grating
        roll, yaw, tilt = KeckDEIMOSSpectrograph._grating_orientation(slider, ruling, tilt)

        self.grating = None if ruling == 0 else ReflectionGrating(ruling, tilt, roll, yaw,
                                                                  central_wave=central_wave)
        return self.grating

    def get_detector_map(self):
        """
        Return the DEIMOS detector map.

        Returns:
            :class:`DEIMOSDetectorMap`: The instance describing the detector
            layout for DEIMOS. The object returned is the same as
            :attr:`detector_map`. If :attr:`detector_map` is None when the
            method is called, this method also instantiates it.
        """
        if self.detector_map is None:
            self.detector_map = DEIMOSDetectorMap()
        return self.detector_map

    @staticmethod
    def _grating_orientation(slider, ruling, tilt):
        """
        Return the roll, yaw, and tilt of the provided grating.

        Numbers are hardwired.

        From xidl/DEEP2/spec2d/pro/omodel_params.pro

        Args:
            slider (:obj:`int`):
                The slider position. Should be 2, 3, or 4. If the slider is
                0, the ruling *must* be 0.
            ruling (:obj:`str`, :obj:`int`):
                The grating ruling number. Should be 600, 831, 900, 1200, or
                ``'other'``.
            tilt (:obj:`float`):
                The input grating tilt.

        Returns:
            :obj:`tuple`: The roll, yaw, and tilt of the grating used by the
            optical model.
        """
        if slider == 2 and int(ruling) == 0:
            # Mirror in place of the grating
            return 0., 0., -19.423

        if slider == 2:
            raise ValueError('Ruling should be 0 if slider in position 2.')

        # Use the calibrated coefficients
        # These orientation coefficients are the newest ones and are meant for
        # observations obtained Post-2016 Servicing.
        # TODO: Figure out the impact of these coefficients on the slits identification.
        # We may not need to change them according to when the observations were taken
        _ruling = int(ruling) if int(ruling) in [600, 831, 900, 1200] else 'other'
        orientation_coeffs = {3: {    600: [ 0.145, -0.008, 5.6e-4, -0.146],
                                      831: [ 0.143,  0.000, 5.6e-4, -0.018],
                                      900: [ 0.141,  0.000, 5.6e-4, -0.118],
                                     1200: [ 0.145,  0.055, 5.6e-4, -0.141],
                                  'other': [ 0.145,  0.000, 5.6e-4, -0.141] },
                              4: {    600: [-0.065,  0.063, 6.9e-4, -0.108],
                                      831: [-0.034,  0.060, 6.9e-4, -0.038],
                                      900: [-0.064,  0.083, 6.9e-4, -0.060],
                                     1200: [-0.052,  0.122, 6.9e-4, -0.110],
                                  'other': [-0.050,  0.080, 6.9e-4, -0.110] } }

        # Orientation coefficients meant for observations taken Pre-2016 Servicing
        # orientation_coeffs = {3: {    600: [ 0.145, -0.008, 5.6e-4, -0.182],
        #                               831: [ 0.143,  0.000, 5.6e-4, -0.182],
        #                               900: [ 0.141,  0.000, 5.6e-4, -0.134],
        #                              1200: [ 0.145,  0.055, 5.6e-4, -0.181],
        #                           'other': [ 0.145,  0.000, 5.6e-4, -0.182] },
        #                       4: {    600: [-0.065,  0.063, 6.9e-4, -0.298],
        #                               831: [-0.034,  0.060, 6.9e-4, -0.196],
        #                               900: [-0.064,  0.083, 6.9e-4, -0.277],
        #                              1200: [-0.052,  0.122, 6.9e-4, -0.294],
        #                           'other': [-0.050,  0.080, 6.9e-4, -0.250] } }

        # Return calbirated roll, yaw, and tilt
        return orientation_coeffs[slider][_ruling][0], \
                orientation_coeffs[slider][_ruling][1], \
                tilt*(1-orientation_coeffs[slider][_ruling][2]) \
                    + orientation_coeffs[slider][_ruling][3]

    def get_amapbmap(self, filename):
        """
        Select the pre-grating (amap) and post-grating (bmap) maps according
        to the slider.

        Args:
            filename (:obj:`str`):
                The filename to read the slider information from the header.

        Returns:
            :obj:`tuple`: The two attributes :attr:`amap` and :attr:`bmap`,
            used by the DEIMOS optical model.
        """
        hdu = io.fits_open(filename)

        # Grating slider
        slider = hdu[0].header['GRATEPOS']

        mp_dir = resource_filename('pypeit', 'data/static_calibs/keck_deimos/')

        if slider in [3,4]:
            self.amap = fits.getdata(mp_dir+'amap.s{}.2003mar04.fits'.format(slider))
            self.bmap = fits.getdata(mp_dir+'bmap.s{}.2003mar04.fits'.format(slider))
        else:
            msgs.error('No amap/bmap available for slider {0}. Set `use_maskdesign = False`'.format(slider))
        #TODO: Figure out which amap and bmap to use for slider 2

        return self.amap, self.bmap

    def mask_to_pixel_coordinates(self, x=None, y=None, wave=None, order=1, filename=None,
                                  corners=False):
        r"""
        Convert the mask coordinates in mm to pixel coordinates on the
        DEIMOS detector.

        If not already instantiated, the :attr:`slitmask`,
        :attr:`grating`, :attr:`optical_model`, and :attr:`detector_map`
        attributes are instantiated.  If these are not instantiated, a
        file must be provided.  If no arguments are provided, the
        function expects these attributes to be set and will output the
        pixel coordinates for the centers of the slits in the
        :attr:`slitmask` at the central wavelength of the
        :attr:`grating`.

        Method generally expected to be executed in one of two modes:
            - Use the `filename` to read the slit mask and determine the
              detector positions at the central wavelength.
            - Specifically map the provided x, y, and wave values to the
              detector.

        If arrays are provided for both `x`, `y`, and `wave`, the
        returned objects have the shape :math:`N_\lambda\times S_x`,
        where :math:`S_x` is the shape of the x and y arrays.

        Args:
            x (array-like, optional):
                The x coordinates in the slit mask in mm.  Default is to
                use the center of the slits in the :attr:`slitmask`.
            y (array-like, optional):
                The y coordinates in the slit mask in mm.  Default is to
                use the center of the slits in the :attr:`slitmask`.
            wave (array-like, optional):
                The wavelengths in angstroms for the propagated
                coordinates.  If not provided, an array of wavelength
                covering the full DEIMOS wavelength range will be used.
            order (:obj:`int`, optional):
                The grating order.  Default is 1.
            filename (:obj:`str`, optional):
                The filename to use to (re)instantiate the
                :attr:`slitmask` and :attr:`grating`.  Default is to use
                previously instantiated attributes.
            corners (:obj:`bool`, optional):
                Instead of using the centers of the slits in the
                :attr:`slitmask`, return the detector pixel coordinates
                for the corners of all slits.

        Returns:
            numpy.ndarray: Returns 5 arrays: (1-2) the x and y
            coordinates in the image plane in mm, (3) the detector
            (1-indexed) where the slit should land at the provided
            wavelength(s), and (4-5) the pixel coordinates (1-indexed)
            in the relevant detector.

        Raises:
            ValueError:
                Raised if the user provides one but not both of the x
                and y coordinates, if no coordinates are provided or
                available within the :attr:`slitmask`, or if the
                :attr:`grating`, :attr:`amap` or :attr:`bmap` haven't been
                defined and not file is provided.
        """
        # Cannot provide just one of x or y
        if x is None and y is not None or x is not None and y is None:
            raise ValueError('Must provide both x and y or neither to use slit mask.')

        # Use the file to update the slitmask (if no x coordinates are
        # provided) and the grating
        if filename is not None:
            if x is None and y is None:
                # Reset the slit mask
                self.get_slitmask(filename)
            # Reset the grating
            self.get_grating(filename)
            # Load pre- and post-grating maps
            self.get_amapbmap(filename)

        if self.amap is None and self.bmap is None:
            raise ValueError('Must select amap and bmap; provide a file or use get_amapbmap()')

        # Check that any coordinates are available
        if x is None and y is None and self.slitmask is None:
            raise ValueError('No coordinates; Provide them directly or instantiate slit mask.')

        # Make sure the coordinates are numpy arrays
        _x = None if x is None else np.atleast_1d(x)
        _y = None if y is None else np.atleast_1d(y)
        if _x is None:
            # Use all the slit centers or corners
            _x = self.slitmask.corners[...,0].ravel() if corners else self.slitmask.center[:,0]
            _y = self.slitmask.corners[...,1].ravel() if corners else self.slitmask.center[:,1]

        # Check that the grating is defined
        if self.grating is None:
            raise ValueError('Must define a grating first; provide a file or use get_grating()')

        # Instantiate the optical model or reset it grating
        if self.optical_model is None:
            self.optical_model = DEIMOSOpticalModel(self.grating)
        else:
            self.optical_model.reset_grating(self.grating)

        # Instantiate the detector map, if necessary
        self.get_detector_map()

        # hard-coded for DEIMOS: wavelength array if wave is None
        if wave is None:
            npoints = 250
            wave = np.arange(npoints) * 24. + 4000.

        # Compute the detector image plane coordinates (in pixels)
        x_img, y_img = self.optical_model.mask_to_imaging_coordinates(_x, _y, self.amap, self.bmap,
                                                                      nslits=self.slitmask.nslits,
                                                                      wave=wave, order=order)
        # Reshape if computing the corner positions
        if corners:
            x_img = x_img.reshape(self.slitmask.corners.shape[:2])
            y_img = y_img.reshape(self.slitmask.corners.shape[:2])

        # Use the detector map to convert to the detector coordinates
        return (x_img, y_img) + self.detector_map.ccd_coordinates(x_img, y_img, in_mm=False)

    def get_maskdef_slitedges(self, ccdnum=None, filename=None, debug=None):
        """
        Provides the slit edges positions predicted by the slitmask design using
        the mask coordinates already converted from mm to pixels by the method
        `mask_to_pixel_coordinates`.

        If not already instantiated, the :attr:`slitmask`, :attr:`amap`,
        and :attr:`bmap` attributes are instantiated.  If so, a file must be provided.

        Args:
            ccdnum (:obj:`int`):
                Detector number
            filename (:obj:`str`, optional):
                The filename to use to (re)instantiate the :attr:`slitmask` and :attr:`grating`.
                Default is None, i.e., to use previously instantiated attributes.
            debug (:obj:`bool`, optional):
                Run in debug mode.

        Returns:
            :obj:`tuple`: Three `numpy.ndarray`_ and a :class:`~pypeit.spectrographs.slitmask.SlitMask`.
            Two arrays are the predictions of the slit edges from the slitmask design and
            one contains the indices to order the slits from left to right in the PypeIt orientation

        """
        # Re-initiate slitmask and amap and bmap
        if filename is not None:
            # Reset the slitmask
            self.get_slitmask(filename)
            # Reset the grating
            self.get_grating(filename)
            # Load pre- and post-grating maps
            self.get_amapbmap(filename)

        if self.amap is None and self.bmap is None:
            msgs.error('Must select amap and bmap; provide a file or use get_amapbmap()')

        if self.slitmask is None:
            msgs.error('Unable to read slitmask design info. Provide a file.')

        if ccdnum is None:
            msgs.error('A detector number must be provided')

        # Match left and right edges separately
        # Sort slits in mm from the slit-mask design
        sortindx = np.argsort(self.slitmask.center[:, 0])

        # Left (bottom) and right (top) traces in pixels from optical model (image plane and detector)
        # bottom
        omodel_bcoo = self.mask_to_pixel_coordinates(x=self.slitmask.bottom[:, 0], y=self.slitmask.bottom[:, 1])
        bedge_img, ccd_b, bedge_pix = omodel_bcoo[0], omodel_bcoo[2], omodel_bcoo[3]

        # top
        omodel_tcoo = self.mask_to_pixel_coordinates(x=self.slitmask.top[:, 0], y=self.slitmask.top[:, 1])
        tedge_img, ccd_t, tedge_pix = omodel_tcoo[0], omodel_tcoo[2], omodel_tcoo[3]

        # Per each slit we take the median value of the traces over the wavelength direction. These medians will be used
        # for the cross-correlation with the traces found in the images.
        omodel_bspat = np.zeros(self.slitmask.nslits)
        omodel_tspat = np.zeros(self.slitmask.nslits)

        for i in range(omodel_bspat.size):
            # We "flag" the left and right traces predicted by the optical model that are outside of the
            # current detector, by giving a value of -1.
            # bottom
            omodel_bspat[i] = -1 if bedge_pix[i, ccd_b[i, :] == ccdnum].shape[0] < 10 else \
                              np.median(bedge_pix[i, ccd_b[i, :] == ccdnum])
            # top
            omodel_tspat[i] = -1 if tedge_pix[i, ccd_t[i, :] == ccdnum].shape[0] < 10 else \
                              np.median(tedge_pix[i, ccd_t[i, :] == ccdnum])

            # If a left (or right) trace is outside of the detector, the corresponding right (or left) trace
            # is determined using the pixel position from the image plane.
            whgood = np.where(tedge_img[i, :] > -1e4)[0]
            npt_img = whgood.shape[0] // 2
            # This is hard-coded for DEIMOS, since it refers to the detectors configuration
            whgood = whgood[:npt_img] if ccdnum <= 4 else whgood[npt_img:]
            if omodel_bspat[i] == -1 and omodel_tspat[i] >= 0:
                omodel_bspat[i] = omodel_tspat[i] - np.median((tedge_img - bedge_img)[i, whgood])
            if omodel_tspat[i] == -1 and omodel_bspat[i] >= 0:
                omodel_tspat[i] = omodel_bspat[i] + np.median((tedge_img - bedge_img)[i, whgood])

            # If the `omodel_bspat` is greater than `omodel_tspat` we switch the order
            if omodel_bspat[i] > omodel_tspat[i]:
                invert_order = omodel_bspat[i]
                omodel_bspat[i] = omodel_tspat[i]
                omodel_tspat[i] = invert_order

        # If there are overlapping slits, i.e., omodel_tspat[sortindx][i] > omodel_bspat[sortindx][i+1],
        # move the overlapping edges to be adjacent instead
        for i in range(sortindx.size -1):
            if omodel_tspat[sortindx][i] != -1 and omodel_bspat[sortindx][i+1] != -1 and \
                    omodel_tspat[sortindx][i] > omodel_bspat[sortindx][i+1]:
                diff = omodel_tspat[sortindx][i] - omodel_bspat[sortindx][i+1]
                omodel_tspat[sortindx[i]] -= diff/2.
                omodel_bspat[sortindx[i+1]] += diff/2. + 0.1
                # # Re-check If the `omodel_bspat` is greater than `omodel_tspat` and switch the order.
                # # It may happens if 3 slits are overlapping (true story!)
                # if omodel_bspat[sortindx[i]] > omodel_tspat[sortindx[i]]:
                #     invert_order = omodel_bspat[sortindx[i]]
                #     omodel_bspat[sortindx[i]] = omodel_tspat[sortindx[i]]
                #     omodel_tspat[sortindx[i]] = invert_order

        # This print a QA table with info on the slits (sorted from left to right) that fall in the current detector.
        # The only info provided here is `slitid`, which is called `dSlitId` in the DEIMOS design file. I had to remove
        # `slitindex` because not always matches `SlitName` from the DEIMOS design file.
        if not debug:
            num = 0
            msgs.info('Expected slits on current detector')
            msgs.info('*' * 18)
            msgs.info('{0:^6s} {1:^12s}'.format('N.', 'dSlitId'))
            msgs.info('{0:^6s} {1:^12s}'.format('-' * 5, '-' * 9))
            for i in range(sortindx.shape[0]):
                if omodel_bspat[sortindx][i] != -1 or omodel_tspat[sortindx][i] != -1:
                    msgs.info('{0:^6d} {1:^12d}'.format(num, self.slitmask.slitid[sortindx][i]))
                    num += 1
            msgs.info('*' * 18)

        # If instead we run this method in debug mode, we print more info useful for comparison, for example, with
        # the IDL-based pipeline.
        if debug:
            num = 0
            msgs.info('Expected slits on current detector')
            msgs.info('*' * 92)
            msgs.info('{0:^5s} {1:^10s} {2:^12s} {3:^12s} {4:^14s} {5:^16s} {6:^16s}'.format('N.',
                                                                                             'dSlitId', 'slitLen(mm)',
                                                                                             'slitWid(mm)',
                                                                                             'spat_cen(mm)',
                                                                                             'omodel_bottom(pix)',
                                                                                             'omodel_top(pix)'))
            msgs.info('{0:^5s} {1:^10s} {2:^12s} {3:^12s} {4:^14s} {5:^16s} {6:^14s}'.format('-' * 4, '-' * 9, '-' * 11,
                                                                                             '-' * 11, '-' * 13,
                                                                                             '-' * 18, '-' * 15))
            for i in range(sortindx.size):
                if omodel_bspat[sortindx][i] != -1 or omodel_tspat[sortindx][i] != -1:
                    msgs.info('{0:^5d}{1:^14d} {2:^9.3f} {3:^12.3f} {4:^14.3f}    {5:^16.2f} {6:^14.2f}'
                              .format(num, self.slitmask.slitid[sortindx][i],
                                         self.slitmask.length[sortindx][i],
                                         self.slitmask.width[sortindx][i],
                                         self.slitmask.center[:, 0][sortindx][i],
                                         omodel_bspat[sortindx][i], omodel_tspat[sortindx][i]))
                    num += 1
            msgs.info('*' * 92)

        return omodel_bspat, omodel_tspat, sortindx, self.slitmask

    def list_detectors(self):
        """
        List the detectors of this spectrograph, e.g., array([[1, 2, 3, 4], [5, 6, 7, 8]])
        They are separated if they are split into blue and red detectors

        Returns:
            :obj:`tuple`: An array that lists the detector numbers, and a flag that if True
            indicates that the spectrograph is divided into blue and red detectors. The array has
            shape :math:`(2, N_{dets})` if split into blue and red dets, otherwise shape :math:`(1, N_{dets})`
        """
        dets = np.vstack((np.arange(self.ndet)[:self.ndet//2]+1, np.arange(self.ndet)[self.ndet//2:]+1))
        return dets, True

    def spec1d_match_spectra(self, sobjs):
        """Match up slits in a SpecObjs file
        based on coords.  Specific to DEIMOS

        Args:
            sobjs (:class:`pypeit.specobjs.SpecObjs`): 
                Spec1D objects

        Returns:
            tuple: array of indices for the blue detector, 
                array of indices for the red (matched to the blue)
        """

        # ***FOR THE MOMENT, REMOVE SERENDIPS
        good_obj = sobjs.MASKDEF_OBJNAME != 'SERENDIP'
        
        # MATCH RED TO BLUE VIA RA/DEC
        mb = sobjs['DET'] <=4
        mr = sobjs['DET'] >4

        ridx = np.where(mr & good_obj)[0]
        robjs = sobjs[ridx]

        #rslits = slits[mr]
        #bslits = slits[mb]

        n=0

        # SEARCH ON BLUE FIRST
        bmt = []
        rmt = []
        for ibobj in np.where(mb & good_obj)[0]:

            sobj = sobjs[ibobj]
            mtc = sobj.RA == robjs.RA
            if np.sum(mtc) == 1:
                irobj = int(ridx[mtc])
                if not np.isclose(sobj.DEC, sobjs[irobj].DEC):
                    msgs.error('DEC does not match RA!')
                bmt.append(ibobj)
                rmt.append(irobj)
                # START ARRAY
                #if (n==0):
                #    matches = Table([[obj['name']],[robj['name']],[obj['det']],[robj['det']],\
                #                [obj['objra']],[obj['objdec']],[obj['objname']],[obj['maskdef_id']],[obj['slit']]], \
                #                names=('bname', 'rname','bdet','rdet', 'objra','objdec','objname','maskdef_id','xpos'))
                #if (n > 0):
                #    matches.add_row((obj['name'],robj['name'],obj['det'],robj['det'],\
                #                     obj['objra'],obj['objdec'],obj['objname'],obj['maskdef_id'],obj['slit']))
                #n=n+1
            elif np.sum(mtc)>1:
                msgs.error("Multiple RA matches?!  No good..")

            # TODO - confirm with Marla this block is NG
            '''
            # NO RED MATCH
            if (np.sum(mtc)==-11): 
            #if (np.sum(mtc)==0):        

                if (n==0):
                    matches = Table([[obj['name']],['-1'],[obj['det']],[-1],\
                                [obj['objra']],[obj['objdec']],[obj['objname']],[obj['maskdef_id']],[obj['slit']]], \
                                names=('bname', 'rname','bdet','rdet', 'objra','objdec','objname','maskdef_id','xpos'))
                if (n > 0):
                    matches.add_row((obj['name'],'-1',obj['det'],-1,\
                                    obj['objra'],obj['objdec'],obj['objname'],obj['maskdef_id'],obj['slit']))
                n=n+1
            '''


        # TODO -- Confirm with Marla that this is not used
        '''
        # SEARCH RED OBJECTS FOR NON-MATCHES IN BLUE
        for obj in rslits:

            mtc = (obj['objra'] == bslits['objra'])
            #if (np.sum(mtc)==0):

            #   matches.add_row(('-1',obj['name'],-1,obj['det'],\
            #                        obj['objra'],obj['objdec'],obj['objname'],obj['maskdef_id'],obj['slit']))
            #   n=n+1
        '''

        return np.array(bmt), np.array(rmt)

class DEIMOSOpticalModel(OpticalModel):
    """
    Derived class for the DEIMOS optical model.
    """
    # TODO: Are focal_r_surface (!R_IMSURF) and focal_r_curvature
    # (!R_CURV) supposed to be the same?  If so, consolodate these into
    # a single number.
    def __init__(self, grating):
        super(DEIMOSOpticalModel, self).__init__(
                    20018.4,                # Pupil distance in mm (!PPLDIST, !D_1)
                    2133.6,                 # Radius of the image surface in mm (!R_IMSURF)
                    2124.71,                # Focal-plane radius of curvature in mm (!R_CURV)
                    2120.9,                 # Mask radius of curvature in mm (!M_RCURV)
                    np.radians(6.),         # Mask tilt angle in radians (!M_ANGLE)
                    128.803,                # Mask y zero point in mm (!ZPT_YM)
                    3.378,                  # Mask z zero-point in mm (!MASK_HT0)
                    2197.1,                 # Collimator distance in mm (sys.COL_DST)
                    4394.2,                 # Collimator radius of curvature in mm (!R_COLL)
                    -0.75,                  # Collimator curvature constant (!K_COLL)
                    np.radians(0.002),      # Collimator tilt error in radians (sys.COL_ERR)
                    0.0,                    # Collimator tilt phi angle in radians (sys.COL_PHI)
                    grating,                # DEIMOS grating object
                    np.radians(2.752),      # Camera angle in radians (sys.CAM_ANG)
                    np.pi/2,                # Camera tilt phi angle in radians (sys.CAM_PHI)
                    382.0,                  # Camera focal length in mm (sys.CAM_FOC)
                    DEIMOSCameraDistortion(),   # Object used to apply/remove camera distortions
                    np.radians(0.021),      # ICS rotation in radians (sys.MOS_ROT)
                    [-0.234, -3.822])       # Camera optical axis center in mm (sys.X_OPT,sys.Y_OPT)

        # Include tent mirror
        self.tent_theta = np.radians(71.5-0.5)  # Tent mirror theta angle (sys.TNT_ANG)
        self.tent_phi = np.radians(90.+0.081)   # Tent mirror phi angle (sys.TNT_PHI)

        #TENT MIRROR: this mirror is OK to leave in del-theta,phi
        self.tent_reflection \
                = OpticalModel.get_reflection_transform(self.tent_theta, self.tent_phi)

    def reset_grating(self, grating):
        """
        Reset the grating to the provided input instance.
        """
        self.grating = grating

    def mask_coo_to_grating_input_vectors(self, x, y):
        """
        Propagate rays from the mask plane to the grating.

        Taken from xidl/DEEP2/spec2d/pro/model/pre_grating.pro

        Need to override parent class to add tent mirror reflection.
        """
        r = super(DEIMOSOpticalModel, self).mask_coo_to_grating_input_vectors(x, y)
        # Reflect off the tent mirror and return
        return OpticalModel.reflect(r, self.tent_reflection)


class DEIMOSCameraDistortion:
    """Class to remove or apply DEIMOS camera distortion."""
    def __init__(self):
        self.c0 = 1.
        self.c2 = 0.0457563
        self.c4 = -0.3088123
        self.c6 = -14.917
    
        x = np.linspace(-0.6, 0.6, 1000)
        y = self.remove_distortion(x)
        self.interpolator = interpolate.interp1d(y, x)

    def remove_distortion(self, x):
        x2 = np.square(x)
        return x / (self.c0 + x2 * (self.c2 + x2 * (self.c4 + x2 * self.c6)))

    def apply_distortion(self, y):
        indx = (y > self.interpolator.x[0]) & (y < self.interpolator.x[-1])
        if not np.all(indx):
            warnings.warn('Some input angles outside of valid distortion interval!')
        x = np.zeros_like(y)
        x[indx] = self.interpolator(y[indx])
        return x


class DEIMOSDetectorMap(DetectorMap):
    """
    A map of the center coordinates and rotation of each CCD in DEIMOS.

    !! PIXEL COORDINATES ARE 1-INDEXED !!
    """
    def __init__(self):
        # Number of chips
        self.nccd = 8

        # Number of pixels for each chip in each dimension
        self.npix = np.array([2048, 4096])

        # The size of the CCD pixels in mm
        self.pixel_size = 0.015

        # Nominal gap between each CCD in each dimension in mm
        self.ccd_gap = np.array([1, 0.1])

        # Width of the CCD edge in each dimension in mm
        self.ccd_edge = np.array([0.154, 0.070])

        # Effective size of each chip in each dimension in pixels
        self.ccd_size = self.npix + (2*self.ccd_edge + self.ccd_gap)/self.pixel_size

        # Center coordinates
        origin = np.array([[-1.5,-0.5], [-0.5,-0.5], [ 0.5,-0.5], [ 1.5,-0.5],
                           [-1.5, 0.5], [-0.5, 0.5], [ 0.5, 0.5], [ 1.5, 0.5]])
        offset = np.array([[-20.05, 14.12], [-12.64, 7.25], [0.00, 0.00], [-1.34, -19.92],
                           [-19.02, 16.46], [ -9.65, 8.95], [1.88, 1.02], [ 4.81, -24.01]])
        self.ccd_center = origin * self.ccd_size[None,:] + offset
        
        # Construct the rotation matrix
        self.rotation = np.radians([-0.082, 0.030, 0.0, -0.1206, 0.136, -0.06, -0.019, -0.082])
        cosa = np.cos(self.rotation)
        sina = np.sin(self.rotation)
        self.rot_matrix = np.array([cosa, -sina, sina, cosa]).T.reshape(self.nccd,2,2)

        # ccd_geom.pro has offsets by sys.CN_XERR, but these are all 0.

#def deimos_image_sections(inp, det):
#    """
#    Parse the image for the raw image shape and data sections
#
#    Args:
#        inp (str or `astropy.io.fits.HDUList`_ object):
#        det (int):
#
#    Returns:
#        tuple:
#            shape, dsec, osec, ext_items
#            ext_items is a large tuple of bits and pieces for other methods
#                ext_items = hdu, chips, postpix, image
#    """
#    # Check for file; allow for extra .gz, etc. suffix
#    if isinstance(inp, str):
#        fil = glob.glob(inp + '*')
#        if len(fil) != 1:
#            msgs.error('Found {0} files matching {1}'.format(len(fil), inp + '*'))
#        # Read
#        try:
#            msgs.info("Reading DEIMOS file: {:s}".format(fil[0]))
#        except AttributeError:
#            print("Reading DEIMOS file: {:s}".format(fil[0]))
#        # Open
#        hdu = fits.open(fil[0])
#    else:
#        hdu = inp
#    head0 = hdu[0].header
#
#    # Get post, pre-pix values
#    precol = head0['PRECOL']
#    postpix = head0['POSTPIX']
#    preline = head0['PRELINE']
#    postline = head0['POSTLINE']
#    detlsize = head0['DETLSIZE']
#    x0, x_npix, y0, y_npix = np.array(parse.load_sections(detlsize)).flatten()
#
#
#    # Setup for datasec, oscansec
#    dsec = []
#    osec = []
#
#    # get the x and y binning factors...
#    binning = head0['BINNING']
#    if binning != '1,1':
#        msgs.error("This binning for DEIMOS might not work.  But it might..")
#
#    xbin, ybin = [int(ibin) for ibin in binning.split(',')]
#
#    # DEIMOS detectors
#    nchip = 8
#    if det is None:
#        chips = range(nchip)
#    else:
#        chips = [det-1] # Indexing starts at 0 here
#
#    for tt in chips:
#        x1, x2, y1, y2, o_x1, o_x2, o_y1, o_y2 = indexing(tt, postpix, det=det)
#        # Sections
#        idsec = '[{:d}:{:d},{:d}:{:d}]'.format(y1, y2, x1, x2)
#        iosec = '[{:d}:{:d},{:d}:{:d}]'.format(o_y1, o_y2, o_x1, o_x2)
#        dsec.append(idsec)
#        osec.append(iosec)
#
#    # Create final image (if the full image is requested)
#    if det is None:
#        image = np.zeros((x_npix,y_npix+4*postpix))
#        shape = image.shape
#    else:
#        image = None
#        head = hdu[chips[0]+1].header
#        shape = (head['NAXIS2'], head['NAXIS1']-precol)  # We don't load up the precol
#
#    # Pack up a few items for use elsewhere
#    ext_items = hdu, chips, postpix, image
#    # Return
#    return shape, dsec, osec, ext_items

def indexing(itt, postpix, det=None):
    """
    Some annoying book-keeping for instrument placement.

    Parameters
    ----------
    itt : int
    postpix : int
    det : int, optional

    Returns
    -------

    """
    # Deal with single chip
    if det is not None:
        tt = 0
    else:
        tt = itt
    ii = 2048
    jj = 4096
    # y indices
    if tt < 4:
        y1, y2 = 0, jj
    else:
        y1, y2 = jj, 2*jj
    o_y1, o_y2 = y1, y2

    # x
    x1, x2 = (tt%4)*ii, (tt%4 + 1)*ii
    if det is None:
        o_x1 = 4*ii + (tt%4)*postpix
    else:
        o_x1 = ii + (tt%4)*postpix
    o_x2 = o_x1 + postpix

    # Return
    return x1, x2, y1, y2, o_x1, o_x2, o_y1, o_y2


def deimos_read_1chip(hdu,chipno):
    """ Read one of the DEIMOS detectors

    Args:
        hdu (astropy.io.fits.HDUList):
        chipno (int):

    Returns:
        np.ndarray, np.ndarray:
            data, oscan
    """

    # Extract datasec from header
    datsec = hdu[chipno].header['DATASEC']
    detsec = hdu[chipno].header['DETSEC']
    postpix = hdu[0].header['POSTPIX']
    precol = hdu[0].header['PRECOL']

    x1_dat, x2_dat, y1_dat, y2_dat = np.array(parse.load_sections(datsec)).flatten()
    x1_det, x2_det, y1_det, y2_det = np.array(parse.load_sections(detsec)).flatten()

    # This rotates the image to be increasing wavelength to the top
    #data = np.rot90((hdu[chipno].data).T, k=2)
    #nx=data.shape[0]
    #ny=data.shape[1]


    # Science data
    fullimage = hdu[chipno].data
    data = fullimage[x1_dat:x2_dat,y1_dat:y2_dat]

    # Overscan
    oscan = fullimage[:,y2_dat:]

    # Flip as needed
    if x1_det > x2_det:
        data = np.flipud(data)
        oscan = np.flipud(oscan)
    if y1_det > y2_det:
        data = np.fliplr(data)
        oscan = np.fliplr(oscan)

    # Return
    return data, oscan


def load_wmko_std_spectrum(fits_file:str, outfile=None):
    """Load up a Standard spectrum generated by WMKO IDL scripts
    of the great Greg Wirth

    The SpecObjs generated is checked that it is ready for fluxing

    Args:
        fits_file (str): filename
        outfile ([type], optional): Write the SpecObjs object to a FITS file. Defaults to None.

    Returns:
        specobjs.SpecObjs: object holding the spectra
    """

    # Open up
    hdul = fits.open(fits_file)
    meta = Table(hdul[1].data)
    idl_spec = Table(hdul[2].data)

    # Hope this always works..
    npix = int(len(idl_spec)/2)

    # Generate vacuum wavelengths
    idl_vac = wave.airtovac(idl_spec['WAVELENGTH']*units.AA)

    # Generate SpecObj
    sobj1 = specobj.SpecObj.from_arrays('MultiSlit', idl_vac.value[0:npix],
                                  idl_spec['COUNTS'].data[0:npix], 
                                   1./(idl_spec['COUNTS'].data[0:npix]),
                                   DET=3)
    sobj2 = specobj.SpecObj.from_arrays('MultiSlit', idl_vac.value[npix:],
                                  idl_spec['COUNTS'].data[npix:], 
                                   1./(idl_spec['COUNTS'].data[npix:]), 
                                   DET=7)

    # SpecObjs
    sobjs = specobjs.SpecObjs()
    sobjs.add_sobj(sobj1)
    sobjs.add_sobj(sobj2)

    # Fill in header
    coord = linetools.utils.radec_to_coord((meta['RA'][0], meta['DEC'][0]))
    sobjs.header = dict(EXPTIME=1., 
                        AIRMASS=float(meta['AIRMASS']), 
                        DISPNAME=str(meta['GRATING'][0]), 
                        PYP_SPEC='keck_deimos', 
                        RA=coord.ra.deg, 
                        DEC=coord.dec.deg
                   )

    # Check
    assert sobjs.ready_for_fluxing()

    # Write?
    if outfile is not None:
        sobjs.write_to_fits(sobjs.header, outfile)
        print("Wrote: {}".format(outfile))

    return sobjs