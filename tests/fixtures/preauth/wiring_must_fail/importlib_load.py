import importlib.util
s = importlib.util.spec_from_file_location('pa', 'scripts/preauth.py')
pa = importlib.util.module_from_spec(s)
s.loader.exec_module(pa)
pa.verify_signature(p)
