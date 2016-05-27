from PIL import Image
from colour import Color
import numpy as np
import itertools as it
import warnings
import time
import os
import copy
from tqdm import tqdm as ProgressDisplay
import inspect
import subprocess as sp
import tempfile

from helpers import *

from camera import Camera
from tk_scene import TkSceneRoot
from mobject import Mobject

class Scene(object):
    CONFIG = {
        "camera_config"  : {},
        "frame_duration" : DEFAULT_FRAME_DURATION,
        "construct_args" : [],
    }
    def __init__(self, **kwargs):
        digest_config(self, kwargs)
        self.camera = Camera(**self.camera_config)
        self.frames = []
        self.mobjects = []
        self.num_animations = 0

        self.construct(*self.construct_args)

    def construct(self):
        pass #To be implemented in subclasses

    def __str__(self):
        if hasattr(self, "name"):
            return self.name
        return self.__class__.__name__ + \
               self.args_to_string(*self.construct_args)

    def set_name(self, name):
        self.name = name
        return self

    ### Only these methods should touch the camera

    def set_camera(self, camera):
        self.camera = camera

    def get_frame(self):
        return self.camera.get_image()

    def update_frame(self, mobjects = None, background = None, **kwargs):
        if mobjects is None:
            mobjects = self.mobjects
        if background is not None:
            self.camera.set_image(background)
        else:
            self.camera.reset()
        self.camera.capture_mobjects(mobjects, **kwargs)

    def freeze_background(self):
        self.update_frame()
        self.set_camera(Camera(self.get_frame()))
        self.clear()
    ###

    def add(self, *mobjects):
        """
        Mobjects will be displayed, from background to foreground,
        in the order with which they are entered.
        """
        if not all_elements_are_instances(mobjects, Mobject):
            raise Exception("Adding something which is not a mobject")
        old_mobjects = filter(lambda m : m not in mobjects, self.mobjects)
        self.mobjects = old_mobjects + list(mobjects)
        return self

    def add_mobjects_among(self, values):
        """
        So a scene can just add all mobjects it's defined up to that point
        by calling add_mobjects_among(locals().values())
        """
        mobjects = filter(lambda x : isinstance(x, Mobject), values)
        self.add(*mobjects)
        return self

    def remove(self, *mobjects):
        if not all_elements_are_instances(mobjects, Mobject):
            raise Exception("Removing something which is not a mobject")
        mobjects = filter(lambda m : m in self.mobjects, mobjects)
        if len(mobjects) == 0:
            return
        self.mobjects = filter(lambda m : m not in mobjects, self.mobjects)
        return self

    def bring_to_front(self, mobject):
        self.add(mobject)
        return self

    def bring_to_back(self, mobject):
        self.remove(mobject)
        self.mobjects = [mobject] + self.mobjects
        return self

    def clear(self):
        self.mobjects = []
        return self

    def align_run_times(self, *animations, **kwargs):
        if "run_time" in kwargs:
            run_time = kwargs["run_time"]
        else:
            run_time = animations[0].run_time
        for animation in animations:
            animation.set_run_time(run_time)
        return animations

    def separate_moving_and_static_mobjects(self, *animations):
        moving_mobjects = list(it.chain(*[
            anim.mobject.submobject_family()
            for anim in animations
        ]))
        bundle = Mobject(*self.mobjects)
        static_mobjects = filter(
            lambda m : m not in moving_mobjects, 
            bundle.submobject_family()
        )
        return moving_mobjects, static_mobjects

    def get_time_progression(self, animations):
        run_time = animations[0].run_time
        times = np.arange(0, run_time, self.frame_duration)
        time_progression = ProgressDisplay(times)
        time_progression.set_description("".join([
            "Animation %d: "%self.num_animations,
            str(animations[0]),
            (", etc." if len(animations) > 1 else ""),
        ]))
        return time_progression


    def play(self, *animations, **kwargs):
        if len(animations) == 0:
            raise Warning("Called Scene.play with no animations")
            return
        self.num_animations += 1
        self.add(*[anim.mobject for anim in animations])
        animations = self.align_run_times(*animations, **kwargs)
        moving_mobjects, static_mobjects = \
            self.separate_moving_and_static_mobjects(*animations)
        self.update_frame(
            static_mobjects,
            include_submobjects = False
        )
        static_image = self.get_frame()

        for t in self.get_time_progression(animations):
            for animation in animations:
                animation.update(t / animation.run_time)
            self.update_frame(moving_mobjects, static_image)
            self.frames.append(self.get_frame())
        for animation in animations:
            animation.clean_up()
        return self

    def play_over_time_range(self, t0, t1, *animations):
        needed_scene_time = max(abs(t0), abs(t1))
        existing_scene_time = len(self.frames)*self.frame_duration
        if existing_scene_time < needed_scene_time:
            self.dither(needed_scene_time - existing_scene_time)
            existing_scene_time = needed_scene_time
        #So negative values may be used
        if t0 < 0:
            t0 = float(t0)%existing_scene_time
        if t1 < 0:
            t1 = float(t1)%existing_scene_time
        t0, t1 = min(t0, t1), max(t0, t1)    

        moving_mobjects = [anim.mobject for anim in animations]
        for t in np.arange(t0, t1, self.frame_duration):
            for animation in animations:
                animation.update((t-t0)/(t1 - t0))
            index = int(t/self.frame_duration)
            self.update_frame(moving_mobjects, self.frames[index])
            self.frames[index] = self.get_frame()
        for animation in animations:
            animation.clean_up()
        return self

    def dither(self, duration = DEFAULT_DITHER_TIME):
        self.update_frame()
        self.frames += [self.get_frame()]*int(duration / self.frame_duration)
        return self

    def repeat_frames(self, num):
        self.frames = self.frames*num
        return self

    def reverse_frames(self):
        self.frames.reverse()
        return self

    def invert_colors(self):
        white_frame = 255*np.ones(self.get_frame().shape, dtype = 'uint8')
        self.frames = [
            white_frame-frame
            for frame in self.frames
        ]
        return self

    def show_frame(self):
        self.update_frame()
        Image.fromarray(self.get_frame()).show()

    def preview(self):
        TkSceneRoot(self)

    def save_image(self, directory = MOVIE_DIR, name = None):
        path = os.path.join(directory, "images")
        file_name = (name or str(self)) + ".png"
        full_path = os.path.join(path, file_name)
        if not os.path.exists(path):
            os.makedirs(path)
        Image.fromarray(self.get_frame()).save(full_path)

    def get_movie_file_path(self, name, extension):
        file_path = os.path.join(MOVIE_DIR, name)
        if not file_path.endswith(extension):
            file_path += extension
        directory = os.path.split(file_path)[0]
        if not os.path.exists(directory):
            os.makedirs(directory)
        return file_path

    def write_to_movie(self, name = None):
        if len(self.frames) == 0:
            print "No frames, I'll just save an image instead"
            self.show_frame()
            self.save_image(name = name)
            return
        if name is None:
            name = str(self)

        file_path = self.get_movie_file_path(name, ".mp4")
        print "Writing to %s"%file_path

        fps = int(1/self.frame_duration)
        height, width = self.camera.pixel_shape

        command = [
            FFMPEG_BIN,
            '-y',                 # overwrite output file if it exists
            '-f', 'rawvideo',
            '-vcodec','rawvideo',
            '-s', '%dx%d'%(width, height), # size of one frame
            '-pix_fmt', 'rgb24',
            '-r', str(fps),       # frames per second
            '-i', '-',            # The imput comes from a pipe
            '-an',                # Tells FFMPEG not to expect any audio
            '-vcodec', 'mpeg',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-loglevel', 'error',
            file_path,
        ]
        process = sp.Popen(command, stdin=sp.PIPE)
        for frame in self.frames:
            process.stdin.write(frame.tostring())
        process.stdin.close()
        process.wait()        

    def write_to_gif(self, name = None):
        if len(self.frames) == 0:
            print "No frames, I'll just save an image instead"
            self.show_frame()
            self.save_image(name = name)
            return
        if name is None:
            name = str(self)

        file_path = self.get_movie_file_path(name, ".gif")
        print "Writing to %s"%file_path

        fps = int(1/self.frame_duration)
        height, width = self.camera.pixel_shape

        # details on these flags found at http://superuser.com/a/730389/506167
        ffmpeg_command = [
            FFMPEG_BIN,
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', '%dx%d'%(width, height),
            '-pix_fmt', 'rgb24',
            '-i', '-',
            '-vf', 'scale=iw:-1', # iw here is input width, then -1 means
                                  # calculate the height based on aspect ratio
            '-r', str(fps),
            '-f', 'image2pipe',   # tells ffmpeg to split the video into images
                                  # and make it suitable to be piped out
            '-vcodec', 'ppm',
            '-'
        ]

        # delay value should be in ticks and imagemagick does 100 ticks
        # per second.
        # More reading / discussion:
        # http://www.imagemagick.org/discourse-server/viewtopic.php?t=14739
        # fps = 100 / delay
        # delay = 100 / fps
        delay_value = 100 / fps
        imagemagick_command = [
            IMAGEMAGICK_BIN,
            '-delay', str(delay_value),
            '-loop', '0',
            '-',
            file_path
        ]

        ffmpeg_process = sp.Popen(ffmpeg_command,
                stdin=sp.PIPE,
                stdout=sp.PIPE)
        imagemagick_process = sp.Popen(imagemagick_command,
                stdin=ffmpeg_process.stdout)
        for frame in self.frames:
            ffmpeg_process.stdin.write(frame.tobytes())
        ffmpeg_process.stdin.close()
        imagemagick_process.wait()

        return
        # Below is attempt from http://blog.pkh.me/p/21-high-quality-gif-with-ffmpeg.html
        # worth revisiting because it should give higher quality results
        # begin nonworking dead code

        filterstring = "fps=%s,scale=320:-1:flags=lanczos,palettegen" % str(fps)
        # http://stackoverflow.com/questions/26541416/
        palettefile = next(tempfile._get_candidate_names()) + ".png"

        command = [
            FFMPEG_BIN,
            '-y',                 # overwrite output file if it exists
            '-f', 'rawvideo',
            '-vcodec','rawvideo',
            '-s', '%dx%d'%(width, height), # size of one frame
            '-pix_fmt', 'rgb24',
            '-r', str(fps),       # frames per second
            '-i', '-',            # The imput comes from a pipe
            '-an',                # Tells FFMPEG not to expect any audio
            '-vf', filterstring,
            '-y', palettefile
        ]
        process = sp.Popen(command, stdin=sp.PIPE)
        for frame in self.frames:
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        process.wait()

        command = [
            FFMPEG_BIN,
            '-y',                 # overwrite output file if it exists
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', '%dx%d'%(width, height), # size of one frame
            '-pix_fmt', 'rgb24',
            '-r', str(fps),       # frames per second
            '-i', '-',            # The imput comes from a pipe
            '-i', palettefile,
            '-lavfi', '"' + filterstring + '[x]; [x][1:v] paletteuse' + '"',
            '-loglevel', 'error',
            '-y', file_path
        ]

        process = sp.Popen(command, stdin=sp.PIPE)
        for frame in self.frames:
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        process.wait()
        # end nonworking code


    # To list possible args that subclasses have
    # Elements should always be a tuple
    args_list = []

    # For subclasses to turn args in the above  
    # list into stings which can be appended to the name
    @staticmethod
    def args_to_string(*args):
        return ""
        
    @staticmethod
    def string_to_args(string):
        raise Exception("string_to_args Not Implemented!")



























