***********
Keck DEIMOS
***********

Overview
========

This file summarizes several instrument specific
settings that are related to the Keck/DEIMOS spectrograph.

.. warning::

    ``PypeIt`` currently *cannot* reduce images produced by reading
    the DEIMOS CCDs with the A amplifier or those taken in imaging
    mode. All image-handling assumes DEIMOS images have been read
    with the B amplifier in the "Spectral" observing mode. ``PypeIt``
    handles files that do not meet these criteria in two ways:

        - When running :ref:`pypeit_setup`, any frames not in
          Spectral mode and read by the B amplifier will be ignored
          and should not appear in your :ref:`pypeit_file`.

        - If you add frames to the :ref:`pypeit_file` that are not in
          Spectral mode and read by the B amplifier, the method used
          to read the DEIMOS files will fault.

Deviations
==========

The default changes to the ``PypeIt`` parameters specific to DEIMOS
data are listed here: :ref:`instr_par`.

These are tuned to the standard calibration
set taken with DEIMOS.

Calibrations
============

Edge Tracing
------------

It has been reported that the default `edge_thresh` of 50
for DEIMOS is too high for some setups.  If some of your
'fainter' slits on the blue side of the spectrum are missing,
try::

    [calibrations]
      [[slitedges]]
         edge_thresh = 10

It is possible, however, that our new implementation of using
the slitmask design file has alleviated this issue.

Slit-mask design matching
-------------------------
``PypeIt`` is able to match the traced slit to the slit-mask design information
contained as meta data in the DEIMOS observations. This functionality at the moment is
implemented only for DEIMOS and MOSFIRE and is switched on by setting **use_maskdesign** flag in
:ref:`pypeit_par:EdgeTracePar Keywords` to *True*.  This is, already, the default for DEIMOS,
except when the *LongMirr* or the *LVM* mask is used.

``PypeIt`` also assigns to each extracted 1D spectrum the corresponding RA, Dec and object name
information from the slitmask design, and forces the extraction of undetected object at the location
expected from the slitmask design. See `Additional Reading`_ .

When the extraction of undetected object is performed, a gaussian profile with FWHM given by the parameter
``find_fwhm`` in :ref:`pypeit_par:FindObjPar Keywords` is assumed. The default value for DEIMOS is **find_fwhm = 10**
(pixels), but the user needs to adjust this value according to the size of the objects to extract. Moreover,
it may be occasionally necessary to set **no_local_sky = True** in :ref:`pypeit_par:SkySubPar Keywords`
to avoid a bad local sky subtraction.

Wavelength Calibration
----------------------
``PypeIt`` is able (currently only for DEIMOS) to read from the header of the arc frames which
lamps were ON during the observations and to set those to be the list of lamps to be used
for the wavelength calibration. This functionality is switched on by setting ``lamps = use_header``
in :ref:`pypeit_par:WavelengthSolutionPar Keywords`. This is already set by default for DEIMOS.

It may happen, occasionally, that some lamps are not recorded in the header even if they were ON
during the observations. This could be the case if a specific script, called `calib_blue`
(see https://www2.keck.hawaii.edu/inst/deimos/calib_blue.html), is used to take arc frames for
blue observations. To resolve this, the user can just edit the PypeIt file to input the correct
list of lamps in the following way::

    [calibrations]
      [[wavelengths]]
            lamps = ArI, NeI, KrI, XeI, CdI, ZnI, HgI


Flat Fielding
-------------

When using the *LVMslitC* mask, it is common for the
widest slits to have saturated flat fields.  If so, the
code will exit during flat fielding. You can skip over them
as described in :ref:`flat_fielding:Saturated Slits`.


Fluxing
-------

If you use the LVMslitC (common), avoid placing your standard
star in the right-most slit as you are likely to collide with
a bad column.

Flexure
-------

For most users, the standard flexure correction will be sufficient.
For RV users, you may wish to use the
:ref:`flexure:pypeit_multislit_flexure` script which also means
initially reducing the data without the standard corrections.
See those docs for further details and note it has only been
tested for the 1200 line grating and with redder wavelengths.


Additional Reading
==================

Here are additional docs related to Keck/DEIMOS:

.. toctree::
   :maxdepth: 1

   dev/deimosframes
   dev/deimosconfig
   dev/slitmask_ids
   dev/radec_object
   dev/deimos_wavecalib
   dev/add_missing_obj
   deimos_howto