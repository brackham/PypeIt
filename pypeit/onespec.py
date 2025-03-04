"""
Provides a simple datamodel for a single spectrum.

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""
import inspect

from IPython import embed

import numpy as np

from pypeit import datamodel
from pypeit import io
from pypeit.spectrographs.util import load_spectrograph

class OneSpec(datamodel.DataContainer):
    """
    DataContainer to hold single spectra, e.g., from
    :class:`~pypeit.coadd1d.CoAdd1D`.

    See the datamodel for argument descriptions

    Args:
        wave:
        flux:
        PYP_SPEC:

    Attributes:
        head0 (`astropy.io.fits.Header`):  Primary header
        spect_meta (:obj:`dict`): Parsed meta from the header
        spectrograph (:class:`pypeit.spectrographs.spectrograph.Spectrograph`):
            Build from PYP_SPEC

    """
    version = '1.0.0'

    datamodel = {'wave': dict(otype=np.ndarray, atype=np.floating, descr='Wavelength array (Ang)'),
                 'flux': dict(otype=np.ndarray, atype=np.floating,
                              descr='Flux array in units of counts/s or 10^-17 erg/s/cm^2/Ang'),
                 'ivar': dict(otype=np.ndarray, atype=np.floating,
                              descr='Inverse variance array (matches units of flux)'),
                 'mask': dict(otype=np.ndarray, atype=np.integer,
                              descr='Mask array (1=Good,0=Bad)'),
                 'telluric': dict(otype=np.ndarray, atype=np.floating, descr='Telluric model'),
                 'PYP_SPEC': dict(otype=str, descr='PypeIt: Spectrograph name'),
                 'obj_model': dict(otype=np.ndarray, atype=np.floating,
                                   descr='Object model for tellurics'),
                 'ext_mode': dict(otype=str, descr='Extraction mode (options: BOX, OPT)'),
                 'fluxed': dict(otype=bool, descr='Boolean indicating if the spectrum is fluxed.'),
                 'spect_meta': dict(otype=dict, descr='header dict')}

    @classmethod
    def from_file(cls, ifile):
        """
        Over-load :func:`pypeit.datamodel.DataContainer.from_file`
        to deal with the header

        Args:
            ifile (str):  Filename holding the object

        Returns:
            :class:`OneSpec`:

        """
        hdul = io.fits_open(ifile)
        slf = super(OneSpec, cls).from_hdu(hdul)

        # Internals
        slf.filename = ifile
        slf.head0 = hdul[0].header
        # Meta
        slf.spectrograph = load_spectrograph(slf.PYP_SPEC)
        slf.spect_meta = slf.spectrograph.parse_spec_header(slf.head0)
        #
        return slf

    def __init__(self, wave, flux, PYP_SPEC=None, ivar=None, mask=None, telluric=None,
                 obj_model=None, ext_mode=None, fluxed=None):

        args, _, _, values = inspect.getargvalues(inspect.currentframe())
        _d = dict([(k,values[k]) for k in args[1:]])
        # Setup the DataContainer
        datamodel.DataContainer.__init__(self, d=_d)

    def _init_internals(self):
        self.head0 = None
        self.filename = None
        self.spectrograph = None
        self.spect_meta = None
        self.history = []

    def to_file(self, ofile, primary_hdr=None, history=None, **kwargs):
        """
        Over-load :func:`pypeit.datamodel.DataContainer.to_file`
        to deal with the header

        Args:
            ofile (:obj:`str`): Filename
            primary_hdr (`astropy.io.fits.Header`_, optional):
            **kwargs:  Passed to super.to_file()

        """
        if primary_hdr is None:
            primary_hdr = io.initialize_header(primary=True)
        # Build the header
        if self.head0 is not None and self.PYP_SPEC is not None:
            spectrograph = load_spectrograph(self.PYP_SPEC)
            subheader = spectrograph.subheader_for_spec(self.head0, self.head0,
                                                        extra_header_cards = ['RA_OBJ', 'DEC_OBJ'])
        else:
            subheader = {}
        # Add em in
        for key in subheader:
            primary_hdr[key] = subheader[key]

        # Add history
        if history is not None:
            history.write_to_header(primary_hdr)

        # Do it
        super(OneSpec, self).to_file(ofile, primary_hdr=primary_hdr, **kwargs)



