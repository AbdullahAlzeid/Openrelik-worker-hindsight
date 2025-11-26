# Openrelik worker hindsight
![Openrelik Hindsight Worker](Pics/openrelik_hindsight_repo.png)

## Description
 Parses chromium browsers artifacts with Hindsight parser from a supplied ZIP archive (Native Kape or Velociraptor triage). the tool will execute the following command `hindsight.py -i [Path]` on a given browser profile path (Default folder), then the Worker will provide a txt file including tool's stdout and an xlsx file which is the tool's parsed output.

## Task Configuration
In the currnet release, there are only 2 task configurations available in the worker:
- Browser profile path to parse (Mandatory to provide)
- Password to unlock protected arhcives (Optional)


## Deploy
Update your `config.env` file to set OPENRELIK_WORKER_HINDSIGHT_VERSION to the tagged release version you want to use (or you can use the "latest" tag). Then add the below configuration to the OpenRelik docker-compose.yml file.

```
openrelik-worker-hindsight:
    container_name: openrelik-worker-hindsight
    image: ghcr.io/AbdullahAlzeid/openrelik-worker-hindsight:${OPENRELIK_WORKER_HINDSIGHT_VERSION}
    restart: always
    environment:
      - REDIS_URL=redis://openrelik-redis:6379
      - OPENRELIK_PYDEBUG=0
    volumes:
      - ./data:/usr/share/openrelik/data
    command: "celery --app=src.app worker --task-events --concurrency=4 --loglevel=INFO -Q openrelik-worker-hindsight"
    # ports:
      # - 5678:5678 # For debugging purposes.
```

## Credits
The tool utlized by this worker is developed by [@obsidianforensics](https://github.com/obsidianforensics)
