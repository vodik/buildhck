#!/usr/bin/env python3
# pylint: disable=C0301, R0913, R0914
"""buildhck python client"""

import os, json, shlex
from base64 import b64encode

SETTINGS = {}
SETTINGS['builds_directory'] = 'builds'
SETTINGS['server'] = 'http://localhost:9001'
SETTINGS['auth'] = {}

try:
    import authorization
    SETTINGS['auth'] = authorization.key
    SETTINGS['server'] = authorization.server
except ImportError as exc:
    print("Authorization module was not loaded!")

def s_mkdir(sdir):
    """safe mkdir"""
    if not os.path.exists(sdir):
        os.mkdir(sdir)
    if not os.path.isdir(sdir):
        raise IOError("local path '{}' is not a directory".format(sdir))

def expand_cmd(cmd, replace):
    """expand commands from recipies"""
    cmd_list = shlex.split(cmd)
    for i, item in enumerate(cmd_list):
        for rep in replace:
            if rep in item:
                cmd_list[i] = item.replace(rep, replace[rep])
    print(cmd_list)
    return cmd_list

def run_cmd_catch_output(cmd):
    """run command and catch output and return value"""
    from select import select
    from subprocess import Popen, PIPE
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE)

    log = []
    while True:
        reads = [proc.stdout.fileno(), proc.stderr.fileno()]
        ret = select(reads, [], [])
        for fdi in ret[0]:
            if fdi == proc.stdout.fileno():
                log.append(proc.stdout.readline())
            elif fdi == proc.stderr.fileno():
                log.append(proc.stderr.readline())
        if proc.poll() != None:
            break

    return {'code': proc.returncode, 'output': log}

def run_cmd_list_catch_output(cmd_list, result, expand):
    """run commands in command list and catch output and return code"""
    log = []
    for cmd in cmd_list:
        expanded = expand_cmd(cmd, expand)
        ret = run_cmd_catch_output(expanded)
        if log:
            log.append(b'\n')
        log.append('>> {}:\n'.format(expanded).encode('UTF-8'))
        log.extend(ret['output'])
        if ret['code'] != os.EX_OK:
            result['status'] = 0
            result['log'] = b64encode(b''.join(log)).decode('UTF-8') if log else ''
            return False
    result['status'] = 1 if cmd_list else -1
    result['log'] = b64encode(b''.join(log)).decode('UTF-8') if log else ''
    return True

def prepare(recipe, srcdir, result):
    """prepare project"""
    return run_cmd_list_catch_output(recipe.prepare, result, {'$srcdir': srcdir})

def build(recipe, srcdir, builddir, pkgdir, result):
    """build project"""
    return run_cmd_list_catch_output(recipe.build, result, {'$srcdir': srcdir, '$builddir': builddir, '$pkgdir': pkgdir})

def test(recipe, srcdir, builddir, result):
    """test project"""
    return run_cmd_list_catch_output(recipe.test, result, {'$srcdir': srcdir, '$builddir': builddir})

def package(recipe, srcdir, builddir, pkgdir, result):
    """package project"""
    return run_cmd_list_catch_output(recipe.package, result, {'$srcdir': srcdir, '$builddir': builddir, '$pkgdir': pkgdir})

def clone_git(srcdir, url, branch, result):
    """clone source using git"""
    def git(*args):
        """git wrapper"""
        from subprocess import check_call
        return check_call(['git'] + list(args)) == os.EX_OK

    def git2(*args):
        """git wrapper2"""
        from subprocess import check_output
        return check_output(['git'] + list(args))

    if not branch:
        branch = 'master'

    oldcommit = ''
    if os.path.isdir(os.path.join(srcdir, '.git')):
        os.chdir(srcdir)
        oldcommit = git2('rev-parse', 'HEAD').strip().decode('UTF-8')
        if not git('fetch', '-f', '-u', 'origin', '{}:{}'.format(branch, branch)):
            return False
        if not git('checkout', '-f', '{}'.format(branch)):
            return False
    elif not git('clone', '--depth', '1', '-b', branch, url, srcdir):
        return False

    os.chdir(srcdir)
    result['commit'] = git2('rev-parse', 'HEAD').strip().decode('UTF-8')
    result['description'] = git2('log', '-1', '--pretty=%B').strip().decode('UTF-8')

    if result['commit'] == oldcommit:
        print("no new changes to build...")
        return False

    return True

def download(recipe, srcdir, result):
    """download recipe"""
    proto = ''
    url = ''
    fragment = ''
    branch = ''

    split = recipe.source.split('+', 1)
    if split:
        proto = split[0]
        split = split[1].rsplit('#', 1)
    else:
        split = recipe.source.rsplit('#', 1)

    if split:
        url = split[0]
        fragment = split[1]
    else:
        url = recipe.source

    if fragment:
        branch = fragment.partition("=")[2]
        if branch:
            result['branch'] = branch

    if proto == 'git':
        return clone_git(srcdir, url, branch, result)

    print("unknown protocol")
    return False

def perform_recipe(recipe, srcdir, builddir, pkgdir, result):
    """perform recipe"""
    s_mkdir(srcdir)
    if not download(recipe, srcdir, result):
        return False

    if not prepare(recipe, srcdir, result['build']):
        return True

    s_mkdir(builddir)
    os.chdir(builddir)
    if not build(recipe, srcdir, builddir, pkgdir, result['build']):
        return True

    if not test(recipe, srcdir, builddir, result['test']):
        return True

    s_mkdir(pkgdir)
    os.chdir(builddir)
    if not package(recipe, srcdir, builddir, pkgdir, result['package']):
        return True

    return True

def cook_recipe(recipe):
    """prepare && cook recipe"""
    print('')
    print(recipe.name)
    print(recipe.source)
    print(recipe.build)
    print(recipe.test)
    print(recipe.package)
    print('')

    os.chdir(STARTDIR)
    s_mkdir(SETTINGS['builds_directory'])
    projectdir = os.path.abspath(os.path.join(SETTINGS['builds_directory'], recipe.name))
    builddir = os.path.abspath(os.path.join(projectdir, 'build'))
    srcdir = os.path.abspath(os.path.join(projectdir, 'src'))
    pkgdir = os.path.abspath(os.path.join(projectdir, 'pkg'))

    import socket
    result = {'client': socket.gethostname(),
              'build': {'status': 0},
              'test': {'status': 0},
              'package': {'status': 0}}

    if recipe.github:
        result['github'] = recipe.github

    s_mkdir(projectdir)
    if perform_recipe(recipe, srcdir, builddir, pkgdir, result):
        branch = result.pop('branch', 'unknown')

        key = ''
        if recipe.name in SETTINGS['auth']:
            key = SETTINGS['auth'][recipe.name]
        elif '' in SETTINGS['auth']:
            key = SETTINGS['auth']['']

        import sys, platform
        from urllib.parse import quote
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError
        request = Request('{}/build/{}/{}/{}'.format(SETTINGS['server'],
            quote(recipe.name), quote(branch), quote('{} {}'.format(sys.platform, platform.machine()))))

        request.add_header('Content-Type', 'application/json')
        if key:
            request.add_header('Authorization', key)

        try:
            urlopen(request, json.dumps(result).encode('UTF-8'))
        except HTTPError as exc:
            print("The server couldn't fulfill the request.")
            print('Error code: ', exc.code)
            if exc.code == 400:
                print("Client is broken, wrong syntax given to server")
            elif exc.code == 401:
                print("Wrong key provided for project.")
        except URLError as exc:
            print('Failed to reach a server.')
            print('Reason: ', exc.reason)
        else:
            print('Build successfully sent to server.')

    os.chdir(STARTDIR)

    # import shutil
    # shutil.rmtree(projectdir)

STARTDIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(STARTDIR)

for module in os.listdir('recipes'):
    if module.find("_recipe.py") == -1:
        continue

    modulebase = os.path.splitext(module)[0]
    cook_recipe(getattr(__import__("recipes", fromlist=[modulebase]), modulebase))

#  vim: set ts=8 sw=4 tw=0 :
