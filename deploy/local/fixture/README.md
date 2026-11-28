# Local agent demo

The `local/demo` repository of Cloud Agent's local stack: a small Python
greeting function for agents to inspect and edit. `deploy/local/Dockerfile.repo`
commits this directory on `main` and serves a read-only bare copy at
`git://local-repo/local/demo.git` on the private control-plane network, with
no published port and no pushes, so the runner can clone without GitHub, a
token or a host mount. To change what new clones get, edit this directory and
run `./deploy/local/dev.sh up` from the repository root; existing workspaces
keep their state.
