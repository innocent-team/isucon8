#!/bin/bash -euvx

mkdir redis || true
cd redis
redis-cli -n 0 --raw dump resv_hash | head -c-1 > resv_hash
redis-cli -n 0 --raw dump resv_last_up | head -c-1 > resv_last_up
redis-cli -n 0 --raw dump resv_last_id | head -c-1 > resv_last_id

