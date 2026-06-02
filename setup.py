pip install requests

# Create a webservice user in Robot:
# Settings → Web service and app settings → Create user

export ROBOT_USER="robot-ws-username"
export ROBOT_PASS="robot-ws-password"

# Basic usage — hardware reset, password auth
python3 hetzner_rescue.py 12345

# With SSH key injection + wait for SSH
python3 hetzner_rescue.py 12345 \
  --ssh-key "aa:bb:cc:dd:..." \
  --server-ip 65.21.x.x