from . import slideshow_host
from .. import logger, config
from ..utils.module_ops import get_module
from ..scene.slide_scene import SlideScene
from ..renderer.slide_renderer import DummySlideRenderer
from .slideshow_host import SlideshowHost

import sdl2
import sdl2.ext

def main():
    input_file = config.get_dir("input_file")
    module = get_module(input_file)
    
    if not hasattr(module, "SLIDES"):
        logger.critical("Your module must have a SLIDES array with all of the slides")
        exit(1)

    slides = module.SLIDES

    if not all(issubclass(x, SlideScene) for x in slides):
        logger.critical("All slide classes must inherit from SlideScene")
        exit(1)

    logger.info("Prerendering all slides for thumbnails + cache...")

    prerendered_slides = []
    for scene_cls in slides:
        scene = scene_cls(
            renderer=DummySlideRenderer()
        )
        if not config.disable_caching:
            scene.render()
        prerendered_slides.append(scene)

    logger.info("Starting SDL")

    sdl2.ext.init()

    logger.info("Starting slideshow host!")

    host = SlideshowHost()
    host.run(slides)
