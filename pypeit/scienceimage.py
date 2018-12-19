# Module for the ScienceImage class
from __future__ import absolute_import, division, print_function

import inspect
import numpy as np

import time
import datetime

from astropy import stats

from pypeit import msgs
from pypeit import processimages
from pypeit import specobjs
from pypeit import utils
from pypeit import ginga
from pypeit.core import skysub
from pypeit.core import extract
from pypeit.core import trace_slits
from pypeit.par import pypeitpar
from pypeit.core import procimg


from pypeit.bitmask import BitMask


class ScienceImageBitMask(BitMask):
    """
    Define a bitmask used to set the reasons why each pixel in a science
    image was masked.
    """
    def __init__(self):
        # TODO:
        #   - Can IVAR0 and IVAR_NAN be consolidated into a single bit?
        #   - Is EXTRACT ever set?
        mask = {       'BPM': 'Component of the instrument-specific bad pixel mask',
                        'CR': 'Cosmic ray detected',
                'SATURATION': 'Saturated pixel',
                 'MINCOUNTS': 'Pixel below the instrument-specific minimum counts',
                  'OFFSLITS': 'Pixel does not belong to any slit',
                    'IS_NAN': 'Pixel value is undefined',
                     'IVAR0': 'Inverse variance is undefined',
                  'IVAR_NAN': 'Inverse variance is NaN',
                   'EXTRACT': 'Pixel masked during local skysub and extraction'
               }
        super(ScienceImageBitMask, self).__init__(list(mask.keys()), descr=list(mask.values()))

class ScienceImage():
    """
    This class will organize and run actions related to
    a Science or Standard star exposure

    Parameters
    ----------
    file_list : list
      List of raw files to produce the flat field
    spectrograph : str
    settings : dict-like
    tslits_dict : dict
      dict from TraceSlits class
    tilts : ndarray
      tilts from WaveTilts class
      used for sky subtraction and object finding
    det : int
    setup : str
    datasec_img : ndarray
      Identifies pixels to amplifiers
    bpm : ndarray
      Bad pixel mask
    maskslits : ndarray (bool)
      Specifies masked out slits
    pixlocn : ndarray
    objtype : str
      'science'
      'standard'
    scidx : int
      Row in the fitstbl corresponding to the exposure

    Attributes
    ----------
    frametype : str
      Set to 'science'
    sciframe : ndarray
      Processed 2D frame
    rawvarframe : ndarray
      Variance generated without a sky (or object) model
    modelvarframe : ndarray
      Variance generated with a sky model
    finalvar : ndarray
      Final variance frame
    global_sky : ndarray
      Sky model across the slit/order
    skycorr_box : ndarray
      Local corrections to the sky model
    final_sky : ndarray
      Final sky model; may include 'local' corrections
    obj_model : ndarray
      Model of the object flux
    trcmask : ndarray
      Masks of objects for sky subtraction
    tracelist : list
      List of traces for objects in slits
    inst_name : str
      Short name of the spectrograph, e.g. KASTb
    target_name : str
      Parsed from the Header
    basename : str
      Combination of camera, target, and time
      e.g. J1217p3905_KASTb_2015May20T045733.56
    time : Time
      time object
    specobjs : list
      List of specobjs
    bm: ScienceImageBitMask
      Object used to select bits of a given type
    """

    # Frametype is a class attribute
    frametype = 'science'

    # TODO: Merge into a single parset, one for procing, and one for scienceimage
    def __init__(self, spectrograph, file_list, bg_file_list = [], det=1, objtype='science', binning = None, setup=None,
                 par=None, frame_par=None):

        # Instantiation attributes for this object
        self.spectrograph = spectrograph
        self.file_list = file_list
        self.nsci = len(file_list)
        self.bg_file_list = bg_file_list
        # Are we subtracing the sky using background frames? If yes, set ir_redux=True
        if len(self.bg_file_list) > 0:
            self.nbg = len(self.bg_file_list)
            self.ir_redux = True
        else:
            self.ir_redux = False
            self.nbg = 0
        self.det = det
        self.binning = binning
        self.objtype = objtype
        self.setup = setup

        # Setup the parameters sets for this object
        # NOTE: This uses objtype, not frametype!
        self.par = pypeitpar.ScienceImagePar() if par is None else par
        self.frame_par = pypeitpar.FrameGroupPar(objtype) if frame_par is None else frame_par

        # These attributes will be sert when the image(s) are processed
        self.bpm = None
        self.bias = None
        self.pixel_flat = None
        self.illum_flat = None

        self.steps = []



        # Other attributes that will be set later during object finding,
        # sky-subtraction, and extraction
        self.tslits_dict = None # used by find_object
        self.tilts = None # used by extract
        self.mswave = None # used by extract
        self.maskslits = None # used in find_object and extract
        self.slitmask = None

        # Key outputs images for extraction
        self.sciimg = None
        self.sciivar = None
        self.ivarmodel = None
        self.objimage = None
        self.skyimage = None
        self.global_sky = None
        self.skymask = None
        self.objmask = None
        self.outmask = None
        self.mask = None                        # The composite bit value array
        self.bitmask = ScienceImageBitMask()    # The bit mask interpreter
        self.extractmask = None
        # SpecObjs object
        self.sobjs_obj = None # Only object finding but no extraction
        self.sobjs = None  # Final extracted object list with trace corrections applied

        # Other bookeeping internals
        self.crmask = None
        self.mask = None


    def _chk_objs(self, items):
        """

        Args:
            items:

        Returns:

        """
        for obj in items:
            if getattr(self, obj) is None:
                msgs.warn('You need to generate {:s} prior to this step..'.format(obj))
                if obj in ['sciimg', 'sciivar', 'rn2_img']:
                    msgs.warn('Run the process() method')
                elif obj in ['sobjs_obj']:
                    msgs.warn('Run the find_objects() method')
                elif obj in['global_sky']:
                    msgs.warn('Run the global_skysub() method')
                elif obj in ['tilts', 'tslits_dict'] :
                    msgs.warn('Calibrations missing: these were required to run find_objects() '
                              'and global_skysub()')
                elif obj in ['waveimg']:
                    msgs.warn('Calibrations missing: waveimg must be input as a parameter. Try '
                              'running calibrations')
                return False
        return True

    def find_objects(self, tslits_dict, maskslits=None, skysub=True, show_peaks=False,
                     show_fits=False, show_trace=False, show=False):
        """
        Find objects in the slits. This is currently setup only for ARMS

        Wrapper to extract.objfind

        Parameters
        ----------
        tslits_dict: dict
           Dictionary containing information on the slits traced for this image

        Optional Parameters
        -------------------
        SHOW_PEAKS:  bool
          Generate QA showing peaks identified by object finding

        SHOW_FITS:  bool
          Generate QA  showing fits to traces

        SHOW_TRACE:  bool
          Generate QA  showing traces identified. Requires an open ginga RC modules window

        Returns
        -------
        self.specobjs : Specobjs object
                Container holding Specobj objects
        self.skymask : ndarray
                Boolean image indicating which pixels are useful for global sky subtraction
        self.objmask : ndarray
                Boolean image indicating which pixels have object flux on them

        """

        self.tslits_dict = tslits_dict
        self.maskslits = self._get_goodslits(maskslits)
        gdslits = np.where(~self.maskslits)[0]

        # Build and assign the slitmask and input mask if they do not already exist
        self.slitmask = self.spectrograph.slitmask(tslits_dict, binning=self.binning) if self.slitmask is None else self.slitmask
        self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, slitmask = self.slitmask) if self.mask is None else self.mask


        # create the ouptut images skymask and objmask
        self.skymask = np.zeros_like(self.sciimg,dtype=bool)
        self.objmask = np.zeros_like(self.sciimg,dtype=bool)

        # If we are object finding on the sky subtracted image, then
        # check that the global sky exists
        if skysub is True:
            if self.global_sky is None:
                msgs.error('Object finding on sky subtracted image requested, but global_sky '
                           'is not set. Run global_skysub() first')
            image = self.sciimg - self.global_sky
        else:
            image = self.sciimg

        # Instantiate the specobjs container
        sobjs = specobjs.SpecObjs()

        # Loop on slits
        for slit in gdslits:
            qa_title ="Finding objects on slit # {:d}".format(slit)
            msgs.info(qa_title)
            thismask = (self.slitmask == slit)
            inmask = (self.mask == 0) & (self.crmask == False) & thismask
            # Find objects
            specobj_dict = {'setup': self.setup, 'slitid': slit,
                            'det': self.det, 'objtype': self.objtype}

            # TODO we need to add QA paths and QA hooks. QA should be
            # done through objfind where all the relevant information
            # is. This will be a png file(s) per slit.
            sobjs_slit, self.skymask[thismask], self.objmask[thismask] \
                = extract.objfind(image, thismask, self.tslits_dict['lcen'][:,slit],self.tslits_dict['rcen'][:,slit],
                                  inmask=inmask,hand_extract_dict=self.par['manual'],specobj_dict=specobj_dict, show_peaks=show_peaks,
                                  show_fits=show_fits, show_trace=show_trace,qa_title=qa_title,
                                  nperslit=self.par['maxnumber'])
            sobjs.add_sobj(sobjs_slit)
#            self.qa_proc_list += proc_list

        # TODO Add a hook on ir_redux here to find and mask negative objects if we are difference imaging
        # Finish
        self.sobjs_obj = sobjs
        self.nobj = len(sobjs)

        # Steps
        self.steps.append(inspect.stack()[0][3])
        if show:
            self.show('image', image=image*(self.mask == 0), chname = 'objfind',
                      sobjs=self.sobjs_obj, slits=True)

        # Return
        return self.sobjs_obj, self.nobj


    def global_skysub(self, tslits_dict, tilts, use_skymask=True, update_crmask = True, maskslits=None, show_fit=False,
                      show=False, show_objs=False):
        """
        Perform global sky subtraction, slit by slit

        Wrapper to skysub.global_skysub

        Parameters
        ----------
        tslits_dict: dict
           Dictionary containing information on the slits traced for this image

        Optional Parameters
        -------------------
        bspline_spaceing: (float):
           Break-point spacing for bspline

        use_skymask: (bool, optional):
           Mask objects using self.skymask if object finding has been run
           (This requires they were found previously, i.e. that find_objects was already run)

        Returns:
            global_sky: (numpy.ndarray) image of the the global sky model
        """

        self.tslits_dict = tslits_dict
        self.tilts = tilts
        self.maskslits = self._get_goodslits(maskslits)
        gdslits = np.where(~self.maskslits)[0]

        # Build and assign the slitmask and input mask if they do not already exist
        self.slitmask = self.spectrograph.slitmask(tslits_dict, binning=self.binning) if self.slitmask is None else self.slitmask
        self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, slitmask = self.slitmask) if self.mask is None else self.mask

        # Prep
        self.global_sky = np.zeros_like(self.sciimg)

        show_fit = True # TESTING
        # Mask objects using the skymask? If skymask has been set by
        # objfinding, and masking is requested, then do so
        skymask = self.skymask if ((self.skymask is not None) & use_skymask) \
                        else np.ones_like(self.sciimg, dtype=bool)
        # Loop on slits
        for slit in gdslits:
            msgs.info("Global sky subtraction for slit: {:d}".format(slit))
            thismask = (self.slitmask == slit)
            inmask = (self.mask == 0) & (self.crmask == False) & thismask & skymask
            # Find sky
            self.global_sky[thismask] =  skysub.global_skysub(self.sciimg, self.sciivar,
                                                              self.tilts, thismask,
                                                              self.tslits_dict['lcen'][:,slit],
                                                              self.tslits_dict['rcen'][:,slit],
                                                              inmask=inmask,
                                                              bsp=self.par['bspline_spacing'],
                                                              pos_mask = ~self.ir_redux,
                                                              show_fit=show_fit)
            # Mask if something went wrong
            if np.sum(self.global_sky[thismask]) == 0.:
                self.maskslits[slit] = True

        if update_crmask:
            # Update the crmask by running LA cosmics again
            self.crmask = self.build_crmask(self.sciimg - self.global_sky, ivar = self.sciivar)
            # Rebuild the mask with this new crmask
            self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, slitmask = self.slitmask)

        # Step
        self.steps.append(inspect.stack()[0][3])

        if show:
            sobjs_show = None if show_objs else self.sobjs_obj
            # Global skysub is the first step in a new extraction so clear the channels here
            self.show('global', slits=True, sobjs =sobjs_show, clear=False)


        # Return
        return self.global_sky

    def get_init_sky(self, tslits_dict, tilts, maskslits=None, update_crmask = True, show_fit = False, show = False):

        self.sobjs_obj_init, self.nobj_init = self.find_objects(tslits_dict, skysub=False,maskslits=maskslits)

        # Global sky subtraction, first pass. Uses skymask from object finding step above
        self.global_sky = self.global_skysub(tslits_dict,tilts, use_skymask=True, update_crmask = update_crmask, maskslits=maskslits,
                                             show_fit = show_fit, show=show)

        return self.global_sky

    def get_ech_objects(self, tslits_dict, std_trace = None, show=False, show_peaks=False, show_fits=False, show_trace = False, debug=False):

        # Did they run process?
        if not self._chk_objs(['sciimg', 'sciivar', 'global_sky']):
            msgs.error('All quantities necessary to run ech_objfind() have not been set.')

        # Check for global sky if it does not exist print out a warning

        # Somehow implmenent masking below? Not sure it is worth it
        #self.maskslits = self._get_goodslits(maskslits)
        #gdslits = np.where(~self.maskslits)[0]

        # Build and assign the slitmask and input mask if they do not already exist
        self.slitmask = self.spectrograph.slitmask(tslits_dict, binning=self.binning) if self.slitmask is None else self.slitmask
        self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, slitmask = self.slitmask) if self.mask is None else self.mask

        plate_scale = self.spectrograph.order_platescale(binning=self.binning)
        # ToDO implement parsets here!
        inmask = (self.mask == 0) & (self.crmask == False)
        self.sobjs_ech = extract.ech_objfind(self.sciimg-self.global_sky, self.sciivar, self.slitmask, tslits_dict['lcen'], tslits_dict['rcen'],
                                             inmask=inmask, plate_scale=plate_scale, std_trace=std_trace,ncoeff=5,
                                             sig_thresh=5., show_peaks=show_peaks, show_fits=show_fits, show_trace=show_trace, debug=debug)


        self.nobj_ech = len(self.sobjs_ech)


        # Steps
        self.steps.append(inspect.stack()[0][3])
        if show:
            self.show('image', image=(self.sciimg - self.global_sky)*(self.mask == 0), chname = 'ech_objfind',sobjs=self.sobjs_ech, slits=False)

        return self.sobjs_ech, self.nobj_ech

    def local_skysub_extract(self, sobjs, waveimg, maskslits=None, std = False, show_profile=False, show_resids=False,show=False):
        """
        Perform local sky subtraction, profile fitting, and optimal extraction slit by slit

        Wrapper to skysub.local_skysub_extract

        Parameters
        ----------
        sobjs: object
           Specobjs object containing Specobj objects containing information about objects found.
        waveimg: ndarray, shape (nspec, nspat)
           Wavelength map

        Optional Parameters
        -------------------


        Returns:
            global_sky: (numpy.ndarray) image of the the global sky model
        """


        self.waveimg = waveimg
        # get the good slits and assign self.maskslits
        self.maskslits = self._get_goodslits(maskslits)
        gdslits = np.where(~self.maskslits)[0]

        # Build and assign the slitmask and input mask if they do not already exist
        self.slitmask = self.spectrograph.slitmask(self.tslits_dict, binning=self.binning) if self.slitmask is None else self.slitmask
        #self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, slitmask = self.slitmask) if self.mask is None else self.mask

        if not self._chk_objs([ # Did they run process?
                                'sciimg', 'sciivar', 'rn2img',
                                # Did they run global sky subtraction, self.global_skysub()?
                                'global_sky',
                                # Did the input the right calibrations in prev steps?
                                'tilts', 'waveimg', 'tslits_dict']):
            msgs.error('All quantities necessary to run local_skysub_extract() have not been set.')

        # Allocate the images that are needed
        # Initialize to mask in case no objects were found
        self.outmask = np.copy(self.mask)
        # Initialize to input mask in case no objects were found
        self.extractmask = (self.mask == 0)
        # Initialize to zero in case no objects were found
        self.objmodel = np.zeros_like(self.sciimg)
        # Set initially to global sky in case no objects were found
        self.skymodel  = np.copy(self.global_sky)
        # Set initially to sciivar in case no obects were found.
        self.ivarmodel = np.copy(self.sciivar)

        # Could actually create a model anyway here, but probably
        # overkill since nothing is extracted

        self.sobjs = sobjs.copy()
        # Loop on slits
        for slit in gdslits:
            msgs.info("Local sky subtraction and extraction for slit: {:d}".format(slit))
            thisobj = (self.sobjs.slitid == slit) # indices of objects for this slit
            if np.any(thisobj):
                thismask = (self.slitmask == slit) # pixels for this slit
                # True  = Good, False = Bad for inmask
                inmask = (self.mask == 0) & (self.crmask == False) & thismask
                # Local sky subtraction and extraction
                self.skymodel[thismask], self.objmodel[thismask], self.ivarmodel[thismask], \
                    self.extractmask[thismask] \
                        = skysub.local_skysub_extract(self.sciimg, self.sciivar, self.tilts,
                                                      self.waveimg, self.global_sky, self.rn2img,
                                                      thismask, self.tslits_dict['lcen'][:,slit],
                                                      self.tslits_dict['rcen'][:, slit],
                                                      self.sobjs[thisobj], std = std,
                                                      bsp=self.par['bspline_spacing'],
                                                      inmask=inmask, show_profile=show_profile,
                                                      show_resids=show_resids)

        # Set the bit for pixels which were masked by the extraction.
        # For extractmask, True = Good, False = Bad
        iextract = (self.mask == 0) & (self.extractmask == False)
        self.outmask[iextract] += np.uint64(2**8)
        # Step
        self.steps.append(inspect.stack()[0][3])

        if show:
            self.show('local', sobjs = self.sobjs, slits= True)
            self.show('resid', sobjs = self.sobjs, slits= True)

        # Clean up any interactive windows that are still up
#        for proc in self.qa_proc_list:
#            proc.terminate()
#            proc.join()

        # Return
        return self.skymodel, self.objmodel, self.ivarmodel, self.outmask, self.sobjs


    def _get_goodslits(self, maskslits):
        """
        Return the slits to be reduce by going through the maskslits
        logic below. If the input maskslits is None it uses previously
        assigned maskslits

        Returns
        -------
        gdslits
            numpy array of slit numbers to be reduced
        """

        # Identify the slits that we want to consider.
        if maskslits is not None:
            # If maskslits was passed in use it, and update self
            self.maskslits = maskslits
        elif (self.maskslits is None):
            # If maskslits was not passed, and it does not exist in self, reduce all slits
            self.maskslits = np.zeros(self.tslits_dict['lcen'].shape[1], dtype=bool)
        else: # Otherwise, if self.maskslits exists, use the previously set maskslits
            pass
        return self.maskslits

    # JFH TODO I think science image should not be a child of Processimages. Then this could be running on a file list
    # I think this would be simpler. Implement this!

    def read_stack(self, files, bias, pixel_flat, bpm, illum_flat, cosmics=False):
        """  Utility function for reading in image stacks using ProcessImages
        Parameters
            file_list:
            bias:
            pixel_flat:
            bpm:
            illum_flat:
        Returns:
        """
        nfiles = len(files)
        for ifile in range(nfiles):
            this_proc = processimages.ProcessImages(self.spectrograph, [files[ifile]], det=self.det,par=self.frame_par['process'])
            # TODO I think trim should be hard wired, and am not letting it be a free parameter
            sciimg = this_proc.process(bias_subtract=bias,pixel_flat=pixel_flat, illum_flat=illum_flat, bpm=bpm, apply_gain=True, trim=True)
            # Allocate the images
            if ifile == 0:
                # numpy is row major so stacking will be fastest with nfiles as the first dimensions
                shape = (nfiles, sciimg.shape[0],sciimg.shape[1])
                sciimg_stack  = np.zeros(shape)
                sciivar_stack = np.zeros(shape)
                rn2img_stack  = np.zeros(shape)
                crmask_stack  = np.zeros(shape,dtype=bool)
                mask_stack  = np.zeros(shape,self.bitmask.minimum_dtype(asuint=True))

            # Construct raw variance image
            rawvarframe = this_proc.build_rawvarframe(trim=True)
            # Mask cosmic rays
            sciivar_stack[ifile,:,:] =  utils.calc_ivar(rawvarframe)
            if cosmics:
                crmask_stack[ifile,:,:] = self.build_crmask(sciimg, ivar=sciivar_stack[ifile,:,:])
            sciimg_stack[ifile,:,:] = sciimg
            # Build read noise squared image
            rn2img_stack[ifile,:,:] = this_proc.build_rn2img()
            # Final mask for this image
            mask_stack[ifile,:,:] = self._build_mask(sciimg, sciivar_stack[ifile,:,:], crmask_stack[ifile,:,:])


        return sciimg_stack, sciivar_stack, rn2img_stack, crmask_stack, mask_stack

    def proc(self, bias, pixel_flat, bpm, illum_flat=None, sigma_clip=False, sigrej=None, maxiters=5, show=False):
        """ Process the image

        Wrapper to ProcessImages.process()

        Needed in part to set self.sciframe, although I could kludge it another way..

        Returns
        -------
        self.sciframe
        self.rawvarframe
        self.crmask

        """
        # Process
        self.bpm = bpm
        self.bias = bias
        self.pixel_flat = pixel_flat
        self.illum_flat = illum_flat

        if self.ir_redux:
            if sigma_clip is True:
                msgs.error('You cannot sigma clip with difference imaging as this will reject objects')
            all_files = self.file_list + self.bg_file_list
            cosmics = False # If we are differencing CR reject after we difference for better performance
            # weights account for possibility of differing number of sci and bg images, i.e.
            #  stack = 1/n_sci \Sum sci  - 1/n_bg \Sum bg
            weights = np.hstack((np.ones(self.nsci)/float(self.nsci),-1.0*np.ones(self.nbg)/float(self.nbg)))
        else:
            all_files = self.file_list
            cosmics = True
            weights = np.ones(self.nsci)/float(self.nsci)

        sciimg_stack, sciivar_stack, rn2img_stack, crmask_stack, mask_stack = \
            self.read_stack(all_files, bias, pixel_flat, bpm, illum_flat, cosmics=cosmics)
        nfiles = len(all_files)
        if sigma_clip and (sigrej is None):
            if self.nsci <= 2:
                sigrej = 100.0 # Irrelevant for only 1 or 2 files, we don't sigma clip below
            elif self.nsci == 3:
                sigrej = 1.1
            elif self.nsci == 4:
                sigrej = 1.3
            elif self.nsci == 5:
                sigrej = 1.6
            elif self.nsci == 6:
                sigrej = 1.9
            else:
                sigrej = 2.0

        # ToDO The bitmask is not being properly propagated here!
        if self.nsci > 1:
            if sigma_clip:
                # sigma clip if we have enough images
                if self.nsci > 2: # cannot sigma clipo for <= 2 images
                    ## TODO THis is not tested!!
                    # JFH ToDO Should we be sigma clipping here at all? What if the two background frames are not
                    # at the same location, this then causes problems?
                    # mask_stack > 0 is a masked value. numpy masked arrays are True for masked (bad) values
                    data = np.ma.MaskedArray(sciimg_stack, (mask_stack > 0))
                    sigclip = stats.SigmaClip(sigma=sigrej, maxiters=maxiters,cenfunc='median')
                    data_clipped = sigclip(data, axis=0, masked=True)
                    outmask_stack = np.invert(data_clipped.mask) # outmask = True are good values
            else:
                outmask_stack = (mask_stack == 0)  # outmask = True are good values

            var_stack = utils.calc_ivar(sciivar_stack)
            weights_stack = np.einsum('i,ijk->ijk',weights,outmask_stack)
            weights_sum = np.sum(weights_stack, axis=0)
            # Masked everwhere nused == 0
            self.crmask = np.sum(crmask_stack,axis=0) == nfiles # Was everywhere a CR
            self.sciimg = np.sum(sciimg_stack*weights_stack,axis=0)/(weights_sum + (weights_sum == 0.0))
            varfinal = np.sum(var_stack*weights_stack**2,axis=0)/(weights_sum + (weights_sum == 0.0))**2
            self.sciivar = utils.calc_ivar(varfinal)
            self.rn2img = np.sum(rn2img_stack*weights_stack**2,axis=0)/(weights_sum + (weights_sum == 0.0))**2
            # ToDO If I new how to add the bits, this is what I would do do create the mask. For now
            # we simply create it using the stacked images and the stacked mask
            #nused = np.sum(outmask_stack,axis=0)
            #self.mask = (nused == 0) * np.sum(mask_stack, axis=0)
            self.mask = self._build_mask(self.sciimg, self.sciivar, self.crmask, mincounts=~self.ir_redux)
        else:
            self.mask  = mask_stack[0,:,:]
            self.crmask = crmask_stack[0,:,:]
            self.sciimg = sciimg_stack[0,:,:]
            self.sciivar = sciivar_stack[0,:,:]
            self.rn2img = rn2img_stack[0,:,:]

        if self.ir_redux:
            self.crmask = self.build_crmask(self.sciimg, ivar=self.sciivar)

        # Show the science image if an interactive run, only show the crmask
        if show:
            # Only mask the CRs in this image
            self.show('image', image=self.sciimg*(self.crmask == 0), chname='sciimg')

        return self.sciimg, self.sciivar, self.rn2img, self.mask, self.crmask

    def build_crmask(self, stack, ivar=None):
        """
        Generate the CR mask frame

        Wrapper to procimg.lacosmic

        Parameters
        ----------
        varframe : ndarray, optional

        Returns
        -------
        self.crmask : ndarray
          1. = Masked CR

        """
        # Run LA Cosmic to get the cosmic ray mask
        proc_par = self.frame_par['process']
        varframe = utils.calc_ivar(ivar)
        saturation = self.spectrograph.detector[self.det-1]['saturation']
        nonlinear = self.spectrograph.detector[self.det-1]['nonlinear']
        sigclip, objlim = self.spectrograph.get_lacosmics_par(proc_par,binning=self.binning)
        crmask = procimg.lacosmic(self.det, stack, saturation, nonlinear,
                                  varframe=varframe, maxiter=proc_par['lamaxiter'],
                                  grow=proc_par['grow'],
                                  remove_compact_obj=proc_par['rmcompact'],
                                  sigclip=sigclip,
                                  sigfrac=proc_par['sigfrac'],
                                  objlim=objlim)

        # Return
        return crmask

    def _build_mask(self, sciimg, sciivar, crmask, mincounts=True, slitmask = None):
        """
        Return the bit value mask used during extraction.
        
        The mask keys are defined by :class:`ScienceImageBitMask`.  Any
        pixel with mask == 0 is valid, otherwise the pixel has been
        masked.  To determine why a given pixel has been masked::

            bitmask = ScienceImageBitMask()
            reasons = bm.flagged_bits(mask[i,j])

        To get all the pixel masked for a specific set of reasons::

            indx = bm.flagged(mask, flag=['CR', 'SATURATION'])

        Returns:
            numpy.ndarray: The bit value mask for the science image.
        """
        # Instatiate the mask
        mask = np.zeros_like(sciimg, dtype=self.bitmask.minimum_dtype(asuint=True))

        # Bad pixel mask
        indx = self.bpm.astype(bool)
        mask[indx] = self.bitmask.turn_on(mask[indx], 'BPM')

        # Cosmic rays
        indx = crmask.astype(bool)
        mask[indx] = self.bitmask.turn_on(mask[indx], 'CR')

        # Saturated pixels
        indx = sciimg >= self.spectrograph.detector[self.det - 1]['saturation']
        mask[indx] = self.bitmask.turn_on(mask[indx], 'SATURATION')

        # Minimum counts
        if mincounts:
            indx = sciimg <= self.spectrograph.detector[self.det - 1]['mincounts']
            mask[indx] = self.bitmask.turn_on(mask[indx], 'MINCOUNTS')

        # Pixels excluded from any slit.  Use a try/except block so that
        # the mask can still be created even if tslits_dict has not
        # been instantiated yet
        # TODO: Is this still necessary?
        if slitmask is not None:
            indx = self.slitmask == -1
            mask[indx] = self.bitmask.turn_on(mask[indx], 'OFFSLITS')

        # Undefined counts
        indx = np.invert(np.isfinite(sciimg))
        mask[indx] = self.bitmask.turn_on(mask[indx], 'IS_NAN')

        # Bad inverse variance values
        indx = np.invert(sciivar > 0.0)
        mask[indx] = self.bitmask.turn_on(mask[indx], 'IVAR0')

        # Undefined inverse variances
        indx = np.invert(np.isfinite(sciivar))
        mask[indx] = self.bitmask.turn_on(mask[indx], 'IVAR_NAN')

        return mask

    def run_the_steps(self):
        """
        Run full the full recipe of calibration steps

        Returns:

        """
        for step in self.steps:
            getattr(self, 'get_{:s}'.format(step))()

    def show(self, attr, image=None, showmask=False, sobjs=None, chname=None, slits=False,clear=False):
        """
        Show one of the internal images

        .. todo::
            Should probably put some of these in ProcessImages

        Parameters
        ----------
        attr : str
          global -- Sky model (global)
          sci -- Processed science image
          rawvar -- Raw variance image
          modelvar -- Model variance image
          crmasked -- Science image with CRs set to 0
          skysub -- Science image with global sky subtracted
          image -- Input image
        display : str, optional
        image : ndarray, optional
          User supplied image to display

        Returns
        -------

        """

        if showmask:
            mask_in = self.mask
            bitmask_in = self.bitmask
        else:
            mask_in = None
            bitmask_in = None

        if attr == 'global':
            # global sky subtraction
            if self.sciimg is not None and self.global_sky is not None and self.mask is not None:
                # sky subtracted image
                image = (self.sciimg - self.global_sky)*(self.mask == 0)
                mean, med, sigma = stats.sigma_clipped_stats(image[self.mask == 0], sigma_lower=5.0,
                                                       sigma_upper=5.0)
                cut_min = mean - 1.0 * sigma
                cut_max = mean + 4.0 * sigma
                ch_name = chname if chname is not None else 'global_sky_{}'.format(self.det)
                viewer, ch = ginga.show_image(image, chname=ch_name, bitmask=bitmask_in,
                                              mask=mask_in, clear=clear, wcs_match=True)
                                              #, cuts=(cut_min, cut_max))
        elif attr == 'local':
            # local sky subtraction
            if self.sciimg is not None and self.skymodel is not None and self.mask is not None:
                # sky subtracted image
                image = (self.sciimg - self.skymodel)*(self.mask == 0)
                mean, med, sigma = stats.sigma_clipped_stats(image[self.mask == 0], sigma_lower=5.0,
                                                       sigma_upper=5.0)
                cut_min = mean - 1.0 * sigma
                cut_max = mean + 4.0 * sigma
                ch_name = chname if chname is not None else 'local_sky_{}'.format(self.det)
                viewer, ch = ginga.show_image(image, chname=ch_name, bitmask=bitmask_in,
                                              mask=mask_in, clear=clear, wcs_match=True)
                                              #, cuts=(cut_min, cut_max))
        elif attr == 'sky_resid':
            # sky residual map with object included
            if self.sciimg is not None and self.skymodel is not None \
                    and self.objmodel is not None and self.ivarmodel is not None \
                    and self.mask is not None:
                image = (self.sciimg - self.skymodel) * np.sqrt(self.ivarmodel)
                image *= (self.mask == 0)
                ch_name = chname if chname is not None else 'sky_resid_{}'.format(self.det)
                viewer, ch = ginga.show_image(image, chname=ch_name, cuts=(-5.0, 5.0),
                                              bitmask=bitmask_in, mask=mask_in, clear=clear,
                                              wcs_match=True)
        elif attr == 'resid':
            # full residual map with object model subtractede
            if self.sciimg is not None and self.skymodel is not None \
                    and self.objmodel is not None and self.ivarmodel is not None \
                    and self.mask is not None:
                # full model residual map
                image = (self.sciimg - self.skymodel - self.objmodel) * np.sqrt(self.ivarmodel)
                image *= (self.mask == 0)
                ch_name = chname if chname is not None else 'resid_{}'.format(self.det)
                viewer, ch = ginga.show_image(image, chname=ch_name, cuts=(-5.0, 5.0),
                                              bitmask=bitmask_in, mask=mask_in, clear=clear,
                                              wcs_match=True)
        elif attr == 'image':
            ch_name = chname if chname is not None else 'image'
            viewer, ch = ginga.show_image(image, chname=ch_name, clear=clear, wcs_match=True)
        else:
            msgs.warn("Not an option for show")

        if sobjs is not None:
            for spec in sobjs:
                color = 'magenta' if spec.hand_extract_flag else 'orange'
                ginga.show_trace(viewer, ch, spec.trace_spat, spec.idx, color=color)

        if slits:
            if self.tslits_dict is not None:
                slit_ids = [trace_slits.get_slitid(self.sciimg.shape, self.tslits_dict['lcen'],
                                                   self.tslits_dict['rcen'], ii)[0]
                                for ii in range(self.tslits_dict['lcen'].shape[1])]

                ginga.show_slits(viewer, ch, self.tslits_dict['lcen'], self.tslits_dict['rcen'],
                                 slit_ids)  # , args.det)

    def __repr__(self):
        txt = '<{:s}: nimg={:d}'.format(self.__class__.__name__,
                                        self.nsci)
        if len(self.steps) > 0:
            txt+= ' steps: ['
            for step in self.steps:
                txt += '{:s}, '.format(step)
            txt = txt[:-2]+']'  # Trim the trailing comma
        txt += '>'
        return txt



