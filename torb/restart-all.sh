#!/bin/sh
set -euvx

sudo systemctl restart torb.python
sudo systemctl restart mariadb
sudo systemctl restart h2o
