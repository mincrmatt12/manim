"""
Contains two different renderers, for the different "passes":

- SlideDataRenderer:
    Logically converts Scenes into Slides, capturing snapshots of scenes before sets of animations for a subslide; creating + caching associated media reasons; and preparing screenshots for transitions.

- LiveSlideRenderer:
    Displays data captured by the SlideDataRenderer into a buffer which is then sent to SDL for display.

    Unlike most other renderers, this has to be able to sync up with the window manager. Unfortunately, manim was really never designed to do that, and so we have to make some compromises.

    The renderer can be in two main "states" of operation:
        - IDLE; where we are simply running updaters and letting the scene progress
        - ANIMATING; where the scene is driving an animation to the screen.

    There are two fundamental differences between these states from a design perspective. In the IDLE state the slideshow host is controlling when new frames are created, whereas in the ANIMATING state
    the scene is "sending" frames to the slideshow host. Despite these differences, in both cases we still ultimately have to sync up with the window manager. Due to this we have to do some creative engineering
    for the ANIMATING case.

    While running an animation, the equivalent of `add_frame` instead sends the frame out to the slideshow host. The host then proceeds to:
        - wait for the right amount of time to match the target framerate
        - show the frame / flip buffers
        - handle events

    In order to do timekeeping, the host independently keeps track of what time it last received a frame and uses this information to calculate how long it should delay. This ensures that (barring lag)
    both:
        - the main "loop" of rendering happens at the framerate AND
        - the time between frames being sent to the window manager is also kept stable.

    That last condition is why we do the delay before showing the frame; in effect allowing the loop (if we unroll all the inverted flow logic):
        while True:
            render_frame()
            wait_for_60hz()
            show_frame()
            handle_events()

    Since the frame display comes immediately after the wait, there's no room for frame-to-frame jitter in calculation time.

    Once the scene tries to render a "subslide" we move back into the IDLE state. Note that we _are_ technically running this from within the scene itself, however we now have full control of the render path.
    Technically speaking, we do in the animation case as well but that requires slightly more invasive modifications into the Scene class (sort of like what the JS renderer does) and I'm trying to avoid that.

    In an IDLE state, the user can either:
        - go back to the last animation's IDLE state
        - advance to the next animation. This is easy to handle, since we can just exit from the "animation" for subslides and let the ANIMATING state logic take over. We do this instead of
          just using the premade copies
"""

import copy
import numpy as np
from ..camera.camera import Camera
from ..utils.exceptions import EndSceneEarlyException
from ..utils.iterables import list_update

class DummySlideRenderer:
    """
    Used to make sure TeX resources / etc. are cached as well as set up thumbnails

    For the most part this could be done by just rendering the video normally but we need the thumbnails
    too.
    """

    def __init__(self):
        self.skip_animations = True
        self.camera = Camera()
        self.num_plays = 0

        self.thumbnail_start = None  # thumbnail from start
        self.thumbnail_end = None    # thumbnail from end

        # thumbnails are in numpy form for ease of rendering.

        self.time = 0
        self.static_image = None

    def init_scene(self, scene):
        pass

    def scene_finished(self, scene):
        self.update_frame(scene, ignore_skipping=True)
        self.thumbnail_end = self.get_frame()

    def play(self, scene, *args, **kwargs):
        self.num_plays += 1

        if self.num_plays == 1:  # first animation (for capturing first frame thumbnail)
            s = scene.compile_animation_data(*args, **kwargs)
            if s is None:
                pass # static
            else:
                # non static
                scene.play_internal(skip_rendering=True)
        else:
            if scene.compile_animation_data(*args, skip_rendering=True, **kwargs):
                scene.play_internal(skip_rendering=True)

    def update_frame(  # TODO Description in Docstring
        self,
        scene,
        mobjects=None,
        include_submobjects=True,
        ignore_skipping=True,
        **kwargs,
    ):

        if self.skip_animations and not ignore_skipping and self.thumbnail_start is not None:
            return
        if mobjects is None:
            mobjects = list_update(
                scene.mobjects,
                scene.foreground_mobjects,
            )
        if self.static_image is not None:
            self.camera.set_frame_to_background(self.static_image)
        else:
            self.camera.reset()

        kwargs["include_submobjects"] = include_submobjects
        self.camera.capture_mobjects(mobjects, **kwargs)

    def save_static_frame_data(self, scene, static_mobjects):
        self.update_frame(scene, mobjects=static_mobjects)
        self.static_image = self.get_frame()
        return self.static_image

    def add_frame(self, frame, num_frames=1):
        self.time += num_frames / self.camera.frame_rate
        if self.thumbnail_start is None:
            self.thumbnail_start = frame

    def get_frame(self):
        return np.array(self.camera.pixel_array)
    

class CairoLiveSlideshowRenderer:
    """A renderer using Cairo.

    num_plays : Number of play() functions in the scene.
    time: time elapsed since initialisation of scene.
    """

    def __init__(self, camera_class=None, **kwargs):
        # All of the following are set to EITHER the value passed via kwargs,
        # OR the value stored in the global config dict at the time of
        # _instance construction_.
        self.file_writer = None
        camera_cls = camera_class if camera_class is not None else Camera
        self.camera = camera_cls()
        self.animations_hashes = []
        self.num_plays = 0
        self.time = 0
        self.static_image = None

        self.slideshow_host = None

    def init_scene(self, scene):
        pass  # TODO: get this

    def init_host(self, host):
        self.slideshow_host = host

    @property
    def skip_animations(self):
        if self.slideshow_host is None:
            return False

        return self.slideshow_host.active_scene.is_skipping_to_end

    def play(self, scene, *args, **kwargs):
        if scene.compile_animation_data(*args, **kwargs):
            scene.play_internal()

    def update_frame(  # TODO Description in Docstring
        self,
        scene,
        mobjects=None,
        include_submobjects=True,
        ignore_skipping=True,
        **kwargs,
    ):
        """Update the frame.

        Parameters
        ----------
        mobjects: list, optional
            list of mobjects

        background: np.ndarray, optional
            Pixel Array for Background.

        include_submobjects: bool, optional

        ignore_skipping : bool, optional

        **kwargs

        """
        if self.skip_animations and not ignore_skipping:
            return
        if mobjects is None:
            mobjects = list_update(
                scene.mobjects,
                scene.foreground_mobjects,
            )
        if self.static_image is not None:
            self.camera.set_frame_to_background(self.static_image)
        else:
            self.camera.reset()

        kwargs["include_submobjects"] = include_submobjects
        self.camera.capture_mobjects(mobjects, **kwargs)

    def render(self, scene, moving_mobjects):
        self.update_frame(scene, moving_mobjects)
        self.add_frame(self.get_frame())

    def get_frame(self):
        """
        Gets the current frame as NumPy array.

        Returns
        -------
        np.array
            NumPy array of pixel values of each pixel in screen.
            The shape of the array is height x width x 3
        """

        return np.array(self.camera.pixel_array)

    def add_frame(self, frame, num_frames=1):
        """
        Adds a frame to the video_file_stream

        Parameters
        ----------
        frame : numpy.ndarray
            The frame to add, as a pixel array.
        num_frames: int
            The number of times to add frame.
        """

        if self.skip_animations:
            return
        for _ in range(num_frames):
            # present frame
            self.slideshow_host.update_with_rendered_frame(self, frame)

    def save_static_frame_data(self, scene, static_mobjects):
        self.update_frame(scene, mobjects=static_mobjects)
        self.static_image = self.get_frame()
        return self.static_image

    def scene_finished(self, scene):
        pass # TODO: send this to things
