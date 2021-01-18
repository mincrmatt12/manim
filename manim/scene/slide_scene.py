from . import scene
from .. import config, logger
from types import MethodType
import enum
import inspect
import functools
import time

__all__ = ["SlideScene"]

class PresentationState(enum.Enum):
    NOT_READY = -1
    ANIMATING = 0
    IDLE = 1

class AnimationGroupEndAction(enum.Enum):
    SUBSLIDE = 0
    EXIT = 1

NON_SLIDESHOW_SUBSLIDE_DELAY = 2.0

class SlideScene(scene.Scene):
    """
    Contains various glue to render a scene in a presentation.

    All slides in a presentation must inherit from this.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.is_skipping_to_end = False # this gets read by the relevant renderer to skip actual frame presentation
        self.anim_generator = None
        self.presentation_state = PresentationState.NOT_READY

        self.is_at_end = False

    def start_render(self):
        """
        Begin rendering this scene
        """

        self.setup()
        if not inspect.isgeneratorfunction(self.construct):
            logger.warn(f"SlideScene {self.__qualname__}'s construct method is not a generator; automatically wrapping it in one.")

            old_construct = self.construct

            @functools.wraps(self.__class__.construct)
            def _wrapper(self):
                return old_construct()
                yield

            self.construct = MethodType(_wrapper, self)
        self.anim_generator = self.construct()
        self.presentation_state = PresentationState.ANIMATING

    def render(self):
        """
        Render this scene fully, for compatibility with rendering the slideshow as a video "normally"

        Here we just un-generator-ify the construct method (if applicable) and run the parent implementation
        """

        if inspect.isgeneratorfunction(self.construct):
            old_construct = self.construct

            @functools.wraps(self.__class__.construct)
            def _wrapper(self):
                for i in old_construct():
                    if i == AnimationGroupEndAction.EXIT:
                        break
                    self.wait(NON_SLIDESHOW_SUBSLIDE_DELAY)
                else:
                    self.wait(NON_SLIDESHOW_SUBSLIDE_DELAY)

            self.construct = MethodType(_wrapper, self)

        super().render()

    def animate_to_next_subslide(self):
        """
        Either starts animating the next subslide or raises an exception if at the end of the slide.

        returns false for finish

        Various tokens can be yielded from the generator to specify end behavior. By default, after the generator
        throws a stopiteration we consider that the last subslide and then raise an exception on the next call.

        sets up for static rendering at the end too
        """

        self.is_skipping_to_end = False
        self.renderer.static_image = None
        self.presentation_state = PresentationState.ANIMATING

        # render next animation
        try:
            result_code = next(self.anim_generator)
        except StopIteration:
            if self.is_at_end:
                return False
            self.is_at_end = True

            result_code = AnimationGroupEndAction.SUBSLIDE

        if result_code == AnimationGroupEndAction.EXIT:
            return False

        # setup for IDLE state
        self.presentation_state = PresentationState.IDLE
        self.update_mobjects(dt=0)
        (
            self.moving_mobjects,
            self.static_mobjects,
        ) = self.get_moving_and_static_mobjects([])
        self.renderer.save_static_frame_data(self, self.static_mobjects)

        return True

    def play_internal(self, skip_rendering=False):
        """
        This method is used to prep the animations for rendering,
        apply the arguments and parameters required to them,
        render them, and write them to the video file.

        Parameters
        ----------
        args
            Animation or mobject with mobject method and params
        kwargs
            named parameters affecting what was passed in ``args``,
            e.g. ``run_time``, ``lag_ratio`` and so on.
        """

        need_last_run = False

        t = 0
        t_0 = time.time()

        while t < self.duration:
            t = time.time() - t_0
            self.update_to_time(t)
            if not skip_rendering:
                self.renderer.render(self, self.moving_mobjects)
            if self.stop_condition is not None and self.stop_condition():
                self.time_progression.close()
                break
            if self.renderer.skip_animations:
                need_last_run = True
                break

        if need_last_run and not skip_rendering:
            self.update_to_time(self.duration)
            self.renderer.render(self, self.moving_mobjects)

        for animation in self.animations:
            animation.finish()
            animation.clean_up_from_scene(self)
        self.renderer.static_image = None

    def skip_to_next_idle_phase(self):
        """
        Sets a flag to ignore rendering all animations and immediately skip to the next IDLE phase, returning from animate_to_next_subslide
        as fast as possible.
        """

        self.is_skipping_to_end = True

    def subslide(self):
        """
        Yield from construct() to break for a subslide
        """

        return AnimationGroupEndAction.SUBSLIDE

    def advance(self):
        """
        Yield from construct() to go to next slide. Useful for 'exit' animation groups.

        By default, returning from a slide func ends in an IDLE state.
        """

        return AnimationGroupEndAction.EXIT
