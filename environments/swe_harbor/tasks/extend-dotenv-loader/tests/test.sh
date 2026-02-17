#!/bin/bash
cd /app

pip install pytest > /dev/null 2>&1

mkdir -p /logs/verifier

pytest /tests/test_solution.py -v 2>&1

if [ $? -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
