#!/usr/bin/env python

import os, sys
from setuptools import setup, find_packages

if not os.getenv('VERSION'):
    print >>sys.stderr, "Please defined a VERSION= variable"
    sys.exit(1)

setup(name='MilkCheck',
      version=os.getenv('VERSION'),
      license='CeCILL',
      description='Parallele command execution manager',
      author='Aurelien Degremont',
      author_email='aurelien.degremont@cea.fr',
      package_dir={'': 'lib'},
      packages=find_packages('lib'),
      scripts=['scripts/milkcheck']
     )
