"""Plugins: folders with a plugin.json that add to Tavern (stage 8а, docs/adr/0009-plugins.md).

  manifest.py  plugin.json, which files a plugin may have, the hash the user approves, zip reading
  install.py   from a file (zip) or a GitHub link; checking for updates
  api.py       PluginAPI: what a plugin's server part gets in `setup(api)`
  manager.py   finding, approving, loading and unloading plugins; commands, actions, model access
"""
