PYTHONPATH=.:../:../../common/:../../../..:../../..:
LIBPATH=.:../../common/
PYTHON=python
COUNT=1
LANG=python
OPTS=0

ifeq ($(OPTS),1)
  _OPTS=--opt
else
  _OPTS=
endif

TESTRUNNER=../../../neco
