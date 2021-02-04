from setuptools import setup, find_packages


with open('README.md') as ins:
    LONG_DESC = ins.read()


with open('requirements.txt') as ins:
    REQUIREMENTS = [line.strip() for line in ins.readlines()]


setup(
    name='wxtrio',
    version=0.2,
    description='Async wxPython with trio.',
    long_description=LONG_DESC,
    author='Jacob Lojewski',
    author_email='',
    url='https://github.com/lojack5/wxtrio',
    license='BSD 3-Clause',
    packages=find_packages(),
    install_requires=REQUIREMENTS,
    python_requires='>=3.8',
    keywords=[
        'async', 'trio', 'wxPython',
        'GUI',
    ],
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Development Status :: 2 - Pre-Alpha',
        'Framework :: Trio',
    ],
)
