# Local embed server has no IPC authentication

The Unix socket (`~/.nokori/embed.sock`) has no token, TLS, or auth mechanism. Threat model: single-user local machine. Any process running as the same UID can connect or send shutdown.

Accepted because Nokori assumes the user's machine is their trust boundary. If `data_dir` is placed on NFS or a shared filesystem, this becomes a risk — documented as user responsibility to keep `0o700` permissions on the data directory.
