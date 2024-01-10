#!/usr/bin/env python3

import argparse
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMIUM_URL = 'https://github.com/photoionization/chromium_source_tarball/releases/download'

def add_depot_tools_to_path(src_dir):
  os.environ['DEPOT_TOOLS_UPDATE'] = '0'
  os.environ['DEPOT_TOOLS_WIN_TOOLCHAIN'] = '0'
  os.environ['CHROMIUM_BUILDTOOLS_PATH'] = os.path.join(src_dir, 'buildtools')
  os.environ['PATH'] = os.pathsep.join([
    os.path.join(src_dir, 'third_party', 'ninja'),
    os.path.join(ROOT_DIR, 'vendor', 'depot_tools'),
    os.environ['PATH'],
  ])

def current_os():
  if sys.platform.startswith('linux'):
    return 'linux'
  elif sys.platform.startswith('win'):
    return 'win'
  elif sys.platform == 'darwin':
    return 'mac'
  else:
    raise ValueError(f'Unsupported platform: {sys.platform}')

def current_cpu():
  arch = platform.machine()
  if arch == 'AMD64' or arch == 'x86_64' or arch == 'x64':
    return 'x64'
  elif arch == 'ARM64':
    return 'arm64'
  elif arch.startswith('ARM'):
    return 'arm'
  else:
    raise ValueError(f'Unrecognized CPU architecture: {arch}')

def download_and_extract(url, extract_path):
  def track_progress(members):
    for index, member in enumerate(members):
      if (index + 1) % 5000 == 0:
        print('.', end='', flush=True)
      yield member
  stream = urllib.request.urlopen(url)
  # Set errorlevel=0 because the tarball may include linux symbolic links that
  # do not exist on current platform.
  with tarfile.open(fileobj=stream, mode='r|xz', errorlevel=0) as tar:
    tar.extractall(path=extract_path, members=track_progress(tar))

def cipd(root, package, version):
  cipd_bin = 'cipd.bat' if current_os() == 'win' else 'cipd'
  args = [ cipd_bin, 'ensure', '-root', root, '-ensure-file', '-' ]
  process = subprocess.Popen(args,
                             text=True,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
  stdout, stderr = process.communicate(input=f'{package} {version}')
  if process.returncode != 0:
    print(stdout)
    print(stderr)
    raise ValueError('cipd failed.')

def read_var_from_deps(deps_file, var):
  result = subprocess.run([ sys.executable,
                            'third_party/depot_tools/gclient.py', 'getdep',
                            '--deps-file', deps_file,
                            '-r', var ],
                          capture_output=True, text=True)
  if result.returncode != 0:
    print(result.stdout)
    print(result.stderr)
    raise ValueError('gclient getdep failed.')
  return result.stdout

def download_from_google_storage(
    bucket, sha_file=None, sha1=None, extract=True, output=None):
  args = [ sys.executable,
           'third_party/depot_tools/download_from_google_storage.py',
           '--no_resume', '--no_auth',
           '--bucket', bucket ]
  if sha1:
    args += [ sha1 ]
  if sha_file:
    args += [ '-s', sha_file ]
  if extract:
    args += [ '--extract' ]
  if output:
    args += [ '-o', output ]
  subprocess.check_call(args)

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--revision', help='The revision to checkout')
  parser.add_argument('--tarball-url', help='Path to Chromium source tarball')
  parser.add_argument('--src-dir', default=os.path.join(ROOT_DIR, 'src'),
                      help='The path of src dir')
  parser.add_argument('--target-cpu', default=current_cpu(),
                      help='Target CPU architecture')
  parser.add_argument('--target-os', default=current_os(),
                      help='Target operating system (win, mac, or linux)')
  args = parser.parse_args()

  if not args.revision and not args.tarball_url:
    print('Must specify either --revision or --tarball-url.')
    return 1

  if args.revision:
    tarball_url = f'{CHROMIUM_URL}/{args.revision}/chromium-{args.revision}.tar.xz'
  else:
    tarball_url = args.tarball_url

  # Download source tarball.
  if not os.path.isdir(args.src_dir):
    tarball_dir = os.path.basename(tarball_url)[:-7]
    if os.path.isdir(tarball_dir):
      print(f'Unable to download tarball since {tarball_dir} exists.')
      return 1

    print('Download and extract', tarball_dir, end='', flush=True)
    download_and_extract(tarball_url, '.')
    print('Done')

    os.rename(tarball_dir, args.src_dir)

  host_os = current_os()
  host_cpu = current_cpu()

  add_depot_tools_to_path(args.src_dir)
  os.chdir(args.src_dir)

  # Download compilers.
  subprocess.check_call([ sys.executable, 'tools/clang/scripts/update.py' ])
  subprocess.check_call([ sys.executable, 'tools/rust/update_rust.py' ])

  # Linux node is always downloaded for remote action.
  download_from_google_storage(
      'chromium-nodejs/16.13.0',
      sha_file='third_party/node/linux/node-linux-x64.tar.gz.sha1')
  # Download util binaries.
  cipd('third_party/devtools-frontend/src/third_party/esbuild',
       'infra/3pp/tools/esbuild/${platform}',
       read_var_from_deps('third_party/devtools-frontend/src/DEPS',
                          'third_party/esbuild:infra/3pp/tools/esbuild/${platform}'))
  cipd('third_party/ninja',
       'infra/3pp/tools/ninja/${platform}',
       read_var_from_deps('DEPS',
                          'src/third_party/ninja:infra/3pp/tools/ninja/${platform}'))
  cipd('buildtools/reclient',
       'infra/rbe/client/${platform}',
       read_var_from_deps('DEPS',
                          'src/buildtools/reclient:infra/rbe/client/${platform}'))
  gn_version = read_var_from_deps('DEPS', 'src/buildtools/mac:gn/gn/mac-${arch}')
  if host_os == 'linux':
    cipd('buildtools/linux64', 'gn/gn/linux-${arch}', gn_version)
    if args.target_os == 'win':
      download_from_google_storage(
          'chromium-browser-clang/rc',
          extract=False,
          sha_file='build/toolchain/win/rc/linux64/rc.sha1')
  elif host_os == 'mac':
    cipd('buildtools/mac', 'gn/gn/mac-${arch}', gn_version)
    download_from_google_storage(
        'chromium-nodejs/16.13.0',
        sha_file=f'third_party/node/mac/node-darwin-{host_cpu}.tar.gz.sha1')
    download_from_google_storage(
        'chromium-browser-clang',
        sha_file=f'tools/clang/dsymutil/bin/dsymutil.{host_cpu}.sha1',
        extract=False,
        output='tools/clang/dsymutil/bin/dsymutil')
    if args.target_os == 'win':
      download_from_google_storage(
          'chromium-browser-clang/rc',
          extract=False,
          sha_file='build/toolchain/win/rc/mac/rc.sha1')
  elif host_os == 'win':
    cipd('buildtools/win', 'gn/gn/windows-amd64', gn_version)
    download_from_google_storage(
        'chromium-nodejs/16.13.0',
        extract=False,
        sha_file='third_party/node/win/node.exe.sha1')
    download_from_google_storage(
        'chromium-browser-clang/rc',
        extract=False,
        sha_file='build/toolchain/win/rc/win/rc.exe.sha1')

  # Download Linux dependencies.
  if host_os == 'linux':
    subprocess.check_call([ sys.executable,
                            'build/linux/sysroot_scripts/install-sysroot.py',
                            '--arch', host_cpu ])
    if host_cpu != args.target_cpu:
      subprocess.check_call([ sys.executable,
                              'build/linux/sysroot_scripts/install-sysroot.py',
                              '--arch', args.target_cpu ])

if __name__ == '__main__':
  exit(main())
