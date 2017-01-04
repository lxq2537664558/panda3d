import collections
import os
import sys
import zipfile

import distutils.command.build
import distutils.core
import distutils.dir_util
import distutils.dist
import distutils.file_util

from direct.showutil import FreezeTool
import panda3d.core as p3d


Application = collections.namedtuple('Application', 'scriptname runtimename')


class Distribution(distutils.dist.Distribution):
    def __init__(self, attrs):
        self.applications = []
        self.directories = []
        self.files = []
        self.exclude_paths = []
        self.exclude_modules = []
        self.wheels = []
        distutils.dist.Distribution.__init__(self, attrs)


# TODO replace with Packager
def find_packages(whlfile):
    if whlfile is None:
        dtool_fn = p3d.Filename(p3d.ExecutionEnvironment.get_dtool_name())
        libdir = os.path.dirname(dtool_fn.to_os_specific())
        filelist = [os.path.join(libdir, i) for i in os.listdir(libdir)]
    else:
        filelist = whlfile.namelist()

    return [i for i in filelist if '.so.' in i or i.endswith('.dll') or i.endswith('.dylib') or 'libpandagl' in i]


class build(distutils.command.build.build):
    def run(self):
        distutils.command.build.build.run(self)
        if not self.distribution.wheels:
            platforms = {p3d.PandaSystem.get_platform(): None}
        else:
            platforms = {
                whl.split('-')[-1].replace('.whl', ''): whl
                for whl in self.distribution.wheels
            }

        for platform, whl in platforms.items():
            builddir = os.path.join(self.build_base, platform)

            if os.path.exists(builddir):
                distutils.dir_util.remove_tree(builddir)
            distutils.dir_util.mkpath(builddir)

            whldir = os.path.join(self.build_base, '__whl_cache__')
            if os.path.exists(whldir):
                distutils.dir_util.remove_tree(whldir)

            if whl is not None:
                whlfile = zipfile.ZipFile(whl)
                stub_path = 'panda3d_tools/deploy-stub'
                if platform.startswith('win'):
                    stub_path += '.exe'
                stub_file = whlfile.open(stub_path)

                # Add whl files to the path so they are picked up by modulefinder
                whlfile.extractall(whldir)
                sys.path.insert(0, whldir)
            else:
                dtool_path = p3d.Filename(p3d.ExecutionEnvironment.get_dtool_name()).to_os_specific()
                stub_path = os.path.join(os.path.dirname(dtool_path), '..', 'bin', 'deploy-stub')
                if platform.startswith('win'):
                    stub_path += '.exe'
                stub_file = open(stub_path, 'rb')


            # Create runtime
            for app in self.distribution.applications:
                freezer = FreezeTool.Freezer()
                freezer.addModule('__main__', filename=app.scriptname)
                for exmod in self.distribution.exclude_modules:
                    freezer.excludeModule(exmod)
                freezer.done(addStartupModules=True)
                freezer.generateRuntimeFromStub(os.path.join(builddir, app.runtimename), stub_file)
                stub_file.close()

            # Copy extension modules
            for module, source_path in freezer.extras:
                if source_path is None:
                    # Built-in module.
                    continue

                # Rename panda3d/core.pyd to panda3d.core.pyd
                basename = os.path.basename(source_path)
                if '.' in module:
                    basename = module.rsplit('.', 1)[0] + '.' + basename

                # Remove python version string
                basename = '.'.join([i for i in basename.split('.') if not i.startswith('cpython-')])

                target_path = os.path.join(builddir, basename)
                distutils.file_util.copy_file(source_path, target_path)

            # Find Panda3D libs
            libs = find_packages(whlfile if whl is not None else None)

            # Copy Panda3D files
            etcdir = os.path.join(builddir, 'etc')
            if whl is None:
                # Libs
                for lib in libs:
                    target_path = os.path.join(builddir, os.path.basename(lib))
                    if not os.path.islink(source_path):
                        distutils.file_util.copy_file(lib, target_path)

                # etc
                dtool_fn = p3d.Filename(p3d.ExecutionEnvironment.get_dtool_name())
                libdir = os.path.dirname(dtool_fn.to_os_specific())
                src = os.path.join(libdir, '..', 'etc')
                distutils.dir_util.copy_tree(src, etcdir)
            else:
                distutils.dir_util.mkpath(etcdir)

                # Combine prc files with libs and copy the whole list
                panda_files = libs + [i for i in whlfile.namelist() if i.endswith('.prc')]
                for pf in panda_files:
                    dstdir = etcdir if pf.endswith('.prc') else builddir
                    target_path = os.path.join(dstdir, os.path.basename(pf))
                    print("copying {} -> {}".format(os.path.join(whl, pf), target_path))
                    with open(target_path, 'wb') as f:
                        f.write(whlfile.read(pf))

            # Copy Game Files
            ignore_copy_list = [
                '__pycache__',
            ] + freezer.getAllModuleNames() + self.distribution.exclude_paths + [i.scriptname for i  in self.distribution.applications]

            for copydir in self.distribution.directories:
                for item in os.listdir(copydir):
                    src = os.path.join(copydir, item)
                    dst = os.path.join(builddir, item)

                    if item in ignore_copy_list:
                        print("skipping", src)
                        continue

                    if os.path.isdir(src):
                        #print("Copy dir", src, dst)
                        distutils.dir_util.copy_tree(src, dst)
                    else:
                        #print("Copy file", src, dst)
                        distutils.file_util.copy_file(src, dst)

            # Copy extra files
            for extra in self.distribution.files:
                if len(extra) == 2:
                    src, dst = extra
                    dst = os.path.join(builddir, dst)
                else:
                    src = extra
                    dst = builddir
                distutils.file_util.copy_file(src, dst)

            # Cleanup whl directory
            if os.path.exists(whldir):
                distutils.dir_util.remove_tree(whldir)


class bdist_panda3d(distutils.core.Command):
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        platforms = [p3d.PandaSystem.get_platform()]
        build_base = os.path.join(os.getcwd(), 'build')

        self.run_command("build")
        os.chdir(build_base)

        for platform in platforms:
            build_dir = os.path.join(build_base, platform)
            base_dir = self.distribution.get_name()
            temp_dir = os.path.join(build_base, base_dir)
            archive_format = 'gztar' if platform.startswith('linux') else 'zip'
            basename = '{}_{}'.format(self.distribution.get_fullname(), platform)

            if (os.path.exists(temp_dir)):
                distutils.dir_util.remove_tree(temp_dir)
            distutils.dir_util.copy_tree(build_dir, temp_dir)

            distutils.archive_util.make_archive(basename, archive_format, root_dir=build_base, base_dir=base_dir)

            distutils.dir_util.remove_tree(temp_dir)

def setup(**attrs):
    attrs.setdefault("distclass", Distribution)
    commandClasses = attrs.setdefault("cmdclass", {})
    commandClasses['build'] = build
    commandClasses['bdist_panda3d'] = bdist_panda3d
    distutils.core.setup(**attrs)
