from .. import logger, config
import time
import enum

from ..renderer.slide_renderer import CairoLiveSlideshowRenderer
from ..scene.slide_scene import PresentationState

import sdl2
import sdl2.ext

import numpy as np

FULLSCREEN = False  # when i can be bothered to figure out the cfg system i'll replace this

class SlideshowEvent(enum.Enum):
    EXIT = 0
    ADVANCE = 1
    PREVIOUS = 2

class SlideshowHost:
    """
    Manages the SDL window for showing live renderings of scenes as well as transitions between said scenes
    """

    def __init__(self):
        self.current_slide_source = None

        self.last_frame_at = None
        self.active_scene = None

        self.last_frame_data = None
        self.slides = None

        # create a sdl window
        self.window = sdl2.ext.Window("manim", size=(config.pixel_width, config.pixel_height), flags=sdl2.SDL_WINDOW_FULLSCREEN if FULLSCREEN else None)

        self.window_surface = sdl2.SDL_CreateRGBSurfaceWithFormat(0, config.pixel_width, config.pixel_height, 32, sdl2.SDL_PIXELFORMAT_ABGR8888)
        self.window_surface_data = sdl2.ext.pixels3d(self.window_surface.contents, transpose=False)

        self.window_real_surface = sdl2.SDL_GetWindowSurface(self.window.window)
        sdl2.SDL_SetSurfaceBlendMode(self.window_real_surface, sdl2.SDL_BLENDMODE_NONE)
        sdl2.SDL_SetSurfaceBlendMode(self.window_surface, sdl2.SDL_BLENDMODE_NONE)

        self.avg_fps = 1.0

    def __del__(self):
        if self.window_surface is not None:
            sdl2.SDL_FreeSurface(self.window_surface)

    def sieve_events(self):
        """
        Go through SDL events and convert them to logical events
        """

        for i in sdl2.ext.get_events(): # TODO
            if i.type == sdl2.SDL_QUIT:
                yield SlideshowEvent.EXIT

            if i.type == sdl2.SDL_KEYDOWN:
                if i.key.windowID != sdl2.SDL_GetWindowID(self.window.window):
                    continue # todo: dispatch to potential speaker view
                if i.key.keysym.sym in [sdl2.SDLK_RIGHT, sdl2.SDLK_DOWN, sdl2.SDLK_SPACE, sdl2.SDLK_RETURN]:
                    yield SlideshowEvent.ADVANCE
            

    def update_framerate_clock(self):
        """
        Delays to keep consecutive calls to this function at the configured frame rate
        """

        if self.last_frame_at is None:
            self.last_frame_at = time.time()
            return 0

        current_time = time.time()
        last = self.last_frame_at

        self.avg_fps = 0.1 * self.avg_fps + 0.9 * (1 / (current_time - last))
        logger.debug("fps: " + str(self.avg_fps))

        if current_time - last < (1 / config.frame_rate):
            off = (1 / config.frame_rate) - (current_time - last)
            sdl2.SDL_Delay(int(off * 1000))

        self.last_frame_at = time.time()
        return self.last_frame_at - last

    def update_with_rendered_frame(self, slide_source, frame_data):
        """
        Sends a rendered frame from an animating slide to the screen. This _does not_ work for the IDLE case, where _this_ class drives the releveant renderer and grabs the frames itself.

        IDLE:
            [ slideshow_host ] -> [ renderer ]
                               <-
            [ slideshow_host ] -> [ renderer.get_frame ]

        ANIM:
            [ slideshow_host ] -> [ scene ] 
                               ->     [ renderer ] -> [slideshow_host]
                               <-

        """

        if id(slide_source) != self.current_slide_source:
            logger.warn(f"Got slides from multiple sources, dropping the one from {slide_source}")
            return

        # pump events
        for evt in self.sieve_events():
            if evt == SlideshowEvent.EXIT:
                logger.critical("don't support exiting during anim, wait")
                pass # todo
            
            elif evt == SlideshowEvent.ADVANCE:
                logger.info("Skipping animation...")
                self.active_scene.skip_to_next_idle_phase()

        # manage delays
        self.update_framerate_clock()
        
        # show frame
        self.send_frame_to_window(frame_data)
        self.last_frame_data = None

    def send_frame_to_window(self, frame):
        np.copyto(self.window_surface_data, frame)
        sdl2.SDL_BlitSurface(self.window_surface, None, self.window_real_surface, None)
        self.window.refresh()

    def render_idle_frame(self):
        """
        Updates the scene and renders it.

        Assumes the renderer is setup correctly as if compile_animation_data had run.
        """

        if self.last_frame_data is None or self.active_scene.should_update_mobjects():
            # update scene mobjects and render
            self.active_scene.update_mobjects(self.update_framerate_clock())
            # actually draw frame
            self.active_scene.renderer.update_frame(self.active_scene, self.active_scene.moving_mobjects)
            # show frame
            frame = self.active_scene.renderer.get_frame()

            self.send_frame_to_window(frame)
            # save frame
            self.last_frame_data = frame

        else:
            self.update_framerate_clock()
            self.send_frame_to_window(self.last_frame_data)

    def set_active_scene(self, scene):
        self.active_scene = scene
        self.active_scene.renderer.init_host(self)
        self.current_slide_source = id(scene.renderer)
        logger.info(f"Setting active scene to {scene!r}")

    def run(self, slides):
        """
        Start running the slideshow. Potentially initialize a speaker view host. Assumes cache-render has completed.
        """

        # TODO: multiple slides lol

        # Initialize all slides with fresh renderers
        self.slides = []
        for slide in slides:
            self.slides.append(
                slide(renderer=CairoLiveSlideshowRenderer())
            )

        # activate the window
        self.window.show()

        # activate first slide
        self.set_active_scene(self.slides[0])

        # TODO: start rendering entrance animations

        self.active_scene.start_render()  # begins animating

        # run an animation
        if not self.active_scene.animate_to_next_subslide():
            return # only one animation

        while True:
            # if we're in this loop, we should be running an IDLE phase, so render an idle frame

            # check first though
            if self.active_scene.presentation_state != PresentationState.IDLE:
                logger.critical("Invalid slideshow state in run()")
                exit(1)

            # maintain frame rate && update screen
            self.render_idle_frame()

            # handle events
            for event in self.sieve_events():
                if event == SlideshowEvent.EXIT:
                    return

                if event == SlideshowEvent.ADVANCE:
                    # handle advancing to next
                    if self.active_scene.animate_to_next_subslide():
                        # continue this thing
                        logger.info("Moved to next subslide")
                        pass
                    else:
                        # go to next thing
                        logger.error("TODO: NI: advance slide")
                        return
