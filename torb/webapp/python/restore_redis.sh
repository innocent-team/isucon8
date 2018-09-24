DIR=/home/isucon/isucon8/torb/webapp/python/redis/

redis-cli -n 0 del resv_hash
redis-cli -n 0 -x restore resv_hash 0 < $DIR/resv_hash 
redis-cli -n 0 del resv_last_up
redis-cli -n 0 -x restore resv_last_up 0 < $DIR/resv_last_up
redis-cli -n 0 del resv_last_id
redis-cli -n 0 -x restore resv_last_id 0 < $DIR/resv_last_id
