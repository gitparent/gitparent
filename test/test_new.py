#!/usr/bin/env python3

'''
Tests `gitp clone` functionality.
'''

import os, sys, shutil, glob
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import utils
from gitparent import cli

@utils.set_workarea()
def test_inject_hierarchy(p):
    ''' Attempt to erroneously add a child repo in the middle of existing repo hierarchy. '''
    top = utils.init_simple_remote('top')
    child = utils.init_simple_remote('child')
    gchild = utils.init_simple_remote('gchild')
    ggchild = utils.init_simple_remote('ggchild')
    cli.clone([top, p])
    os.chdir(p)
    cli.new(['--from', child, 'child'])
    os.chdir('child')
    cli.new(['--from', gchild, 'gchild'])
    os.chdir('gchild')
    os.mkdir(os.path.join('intermediate'))
    cli.new(['--from', ggchild, 'intermediate/ggchild'])
    os.chdir('..')
    os.chdir('..')
    shutil.rmtree('child/gchild/intermediate')
    cli.status([])
    with pytest.raises(Exception):
        cli.new(['--from', ggchild, 'child/gchild/intermediate'])
    cli.status([])

if __name__ == "__main__":
    import pytest
    import sys
    test_inject_hierarchy()
    if len(sys.argv) > 1:
        rc = pytest.main(args=['-s', '-vv', '-k', sys.argv[1], sys.argv[0]])
    else:
        rc = pytest.main(args=['-vv', sys.argv[0]])
    if rc:
        sys.exit(1)