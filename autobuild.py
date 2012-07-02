#!/usr/bin/python3.2
# -*- coding: utf-8 -*-

import os, re, subprocess, sys, time
from configparser import ConfigParser, ExtendedInterpolation
import smtplib
from email.mime.text import MIMEText
"""
This script can be used to automatically build package of a
git repository using Maven.

1. Pull the repository
2. Check if there are some changes and in this case :
   - clean the repository
   - build the package
   - send the package on a remote host

It uses a configuration file parsed by configparser. Default
section 'config' should contains :

| [config]
| user = login
| host = hostname
| remote = remote directory
| modules = mod1,mod2,...

Then each module should have its own section:

| [mod1]
| path = path/to/mod1/repository

Author: Guilhelm Savin
"""

execution_log = False

class ConfigurationError(Exception):
	def __init__(self, value):
		self.value = value

	def __str__(self):
		return repr(self.value)

def execute(args):
	"""
	Fork this process and run a system command using
	the subprocess module. If command failed, a 
	CalledProcessError is raised. Else, the output of
	the command is returned.
	"""
	global execution_log
	
	execution_log.write("Execute \"%s\"\n" % " ".join(args))
	
	try:
		out = subprocess.check_output(args, stderr=execution_log)
		return out.decode('utf-8')
	except subprocess.CalledProcessError as e:
		execution_log.write(e.output.decode('utf-8'))
		raise e

def get_commit(config, module):
	"""
	Return the current commit hash id of a git dir.
	"""
	path = config.get(module, 'path')
	gitdir = os.path.join(path, ".git")
	
	args = []
	args.append('git')
	args.append('--git-dir='+gitdir)
	args.append('log')
	args.append('--pretty=format:%H')
	args.append('-n')
	args.append('1')
	
	return execute(args)

def has_changed(config, module):
	"""
	Check if the git dir module has been updated
	since the last package. This is done by comparing
	last commit hash id with the one contains in the
	cache.
	"""
	cache_path = config.get('config','cache')
	
	if os.path.exists(cache_path):
		cache = ConfigParser()
		cache.read(cache_path)
		
		if not module in cache:
			return True
        
		previous_commit = cache.get(module, 'commit')
		current_commit = get_commit(config, module)

		if previous_commit == current_commit:
			return False
    
	return True

def update_cache(config, module):
	"""
	Update the value of the commit hash id of the
	last package uploaded to the server.
	"""
	cache_path = config.get('config','cache')
	cache = ConfigParser()
	
	if os.path.exists(cache_path):
		cache.read(cache_path)
	
	if not module in cache:
		cache[module] = {}

	commit = get_commit(config, module)
	cache[module]['commit'] = commit
	
	out = open(cache_path, 'w')
	cache.write(out)
	out.flush()
	out.close()

def pull(config, module):
	"""
	Pull the repository of the module. In fact,
	we do not call 'pull' directly but 'fetch' and
	'merge' since there is a bug with git when trying
	to pull a working tree without be inside it.
	"""
	path = config.get(module, 'path')
	gitdir = os.path.join(path, ".git")
	
	args = []
	args.append('git')
	args.append('--git-dir='+gitdir)
	args.append('fetch')
	
	execute(args)
	
	args = []
	args.append('git')
	args.append('--git-dir='+gitdir)
	args.append('--work-tree='+path)
	args.append('merge')
	args.append('origin/master')
	
	execute(args)

def clean(config, module):
	"""
	Clean a maven project.
	"""
	pom = os.path.join(config.get(module, 'path'), 'pom.xml')

	args = []
	args.append('mvn')
	args.append('--file='+pom)
	args.append('clean')

	execute(args)

def build(config, module):
	"""
	Package and install a maven project.
	"""
	pom = os.path.join(config.get(module, 'path'), 'pom.xml')

	args = []
	args.append('mvn')
	args.append('--file='+pom)

	if config.has_option(module, 'profiles'):
		args.append('-P')
		args.append(config.get(module, 'profiles'))
    
	args.append('install')

	execute(args)

def get_pom_infos(config, module):
	pom = os.path.join(config.get(module, 'path'), 'pom.xml')
	fpom = open(pom, 'r')
	cpom = fpom.read()
	fpom.close()
	
	artifactId = re.search('<artifactId>(.*)</artifactId>', cpom).group(1)
	version = re.search('<version>(.*)</version>', cpom).group(1)
	
	yield artifactId
	yield version

def get_jar_name(config, module):
	"""
	Estimate the jar name of the module package. This
	is done by reading the first value of 'artifactId'
	and 'version' in 'pom.xml'. Jar name will be :
	'path/to/repository/target/artifactId-version.jar'.
	"""
	artifactId, version = get_pom_infos(config, module)

	jar = "%s-%s.jar" % (artifactId, version)
	jar = os.path.join("target/", jar)
	jar = os.path.join(config[module]['path'], jar)

	return jar

def get_timestamp(config):
    format = '%Y%m%d-%H%M'

    if 'timestamp_format' in config['config']:
        format = config['config']['timestamp_format']

    return time.strftime(format, time.gmtime())

def upload(config, module):
    """
    Send the package jar to the remote host.
    """
    user = config.get('config', 'user')
    host = config.get('config', 'host')
    remo = config.get('config', 'remote')

    jarname = get_jar_name(config, module)
    dstname = os.path.basename(jarname)
    
    if 'timestamp' in config['config'] and config['config'].getboolean('timestamp'):
        artifactId, version = get_pom_infos(config, module)
        dstname = "%s-%s-%s.jar" % (artifactId, version, get_timestamp(config))

    remo = os.path.join(remo, dstname)

    args = []
    args.append('scp')
    args.append(jarname)
    args.append("{0}@{1}:{2}".format(user, host, remo))

    execute(args)

def check_config(config):
	"""
	Just check if global configuration contains required
	options.
	"""
	required = ['user', 'host', 'remote', 'modules', 'cache']
	
	for r in required:
		if not r in config['config']:
			raise ConfigurationError("missing option '"+r+"'")

def check_module_config(config, module):
	"""
	Just check if module configuration contains required
	options.
	"""
	required = ['path']
	
	for r in required:
		if not config.has_option(module, r):
			raise ConfigurationError("missing option '"+r+"' for module '"+module+"'")
	

def run(config_path):
	"""
	Main function. It reads configuration and build
	modules.
	"""
	global execution_log
	
	config = ConfigParser(interpolation=ExtendedInterpolation())
	config.read(config_path)
	check_config(config)
	
	execution_log = open(config.get('config', 'log'), 'w')
	
	modules = config.get('config', 'modules').split(',')

	errors = []

	for module in modules:
		print("- process module", module)
		check_module_config(config, module)
		
		try:
			pull(config, module)
		except subprocess.CalledProcessError as e:
			print("fail to pull", module, ":", e)
			errors.append("- Failed to pull %s : %s" % (module, e))
		
		if has_changed(config, module):
			try:
				clean(config, module)
				build(config, module)
				upload(config, module)
				update_cache(config, module)
			except subprocess.CalledProcessError as e:
				print('fail to build', module, ":", e)
				errors.append("- Failed to build %s : %s" % (module, e))
	
	if 'hooks' in config and 'rpost' in config['hooks']:
		user = config['config']['user']
		host = config['config']['host']
		args = ['ssh', "%s@%s" % (user, host), config['hooks']['rpost']]
		
		try:
			execute(args)
		except subprocess.CalledProcessError as e:
			print('fail to execute remote post hook:', repr(e))
			errors.append('- Failed to execute remote post hook: %s' % repr(e))
	
	if len(errors) > 0:
		message = "Autobuild has raised some errors :\n"
		message += "\n".join(errors)
		message += "\n\nExecution log :\n\n"
		message += get_log(config)
		
		send_mail(config, "[autobuild] errors", message)
	
	execution_log.close()
		
def get_log(config):
	global execution_log
	
	if execution_log:
		execution_log.flush()
	
	log = open(config.get('config', 'log'), 'r')
	log_data = log.read()
	log.close()
	
	return log_data
		
def send_mail(config, subject, message):
	if 'email' not in config:
		return
	
	sender = config.get('email', 'from')
	receiver = config.get('email', 'contact')
	
	email = MIMEText(message)
	email['Subject'] = subject
	email['From'] = sender
	email['To'] = receiver
	
	server = smtplib.SMTP(config.get('email', 'server'))
	server.sendmail(sender, receiver, email.as_string())
	server.quit()

if len(sys.argv) < 2:
	print("Usage: %s config.cfg" % sys.argv[0])
else:
	config_path = sys.argv[1]
	
	if not os.path.exists(config_path):
		print("file does not exist '%s'" % config_path)
		exit(1)
	
	run(config_path)
