# encoding: utf-8

import urlparse, json, re, itertools
import websocket

class Message(dict):
	"""Dict with attribute access, credits to: http://stackoverflow.com/a/14620633/1393194"""
	def __init__(self, content, *args, **kw):
		if isinstance(content, (str, unicode)):
			content = self.unpack(content)
		elif not hasattr(content, 'iteritems'):
			raise Exception('Message with %s argument' % type(content))
		super(Message, self).__init__(content, *args, **kw)
		self.__dict__ = self

	def pack(self): return json.dumps(self, ensure_ascii=False)
	def unpack(self, content): return json.loads(content)


class Server(websocket.Server):

	def __init__(self, address=None, plugin=None, *args, **kw):
		if not kw.get('handler'): kw['handler'] = Client
		super(Server, self).__init__(address, *args, **kw)
		self.plugin = plugin

	def command(self, cmd, filename='', content=''):
		if not (cmd and len(cmd)): return False
		message = Message({'cmd': cmd, 'filename': filename, 'content': content})
		return self.for_each_client(self.send_command, message)

	def send_command(self, client, message):
		return client.send(message)

	def on_message(self, client, message):
		self.plugin.message(client, message)

	def watch_events(self):
		return self.plugin and self.plugin.watch_events


class Client(websocket.Client):
	"""Stores which files to watch, so there is less traffic when broadcasting events."""

	def __init__(self, *args, **kw):
		super(Client, self).__init__(*args, **kw)
		self.files = {}
		self.patterns = {}
		# these types should match only against the watched file list, but not patterns
		self.noregexp_extensions = ['css', 'less', 'sass', 'scss', 'js']

		# clients should send their full page url (path is /?client=pageurl)
		url = urlparse.urlparse(self.path) # original url parts
		q = urlparse.parse_qs(url.query, True) # params
		url = urlparse.urlparse(q.get('client', [''])[0]) # pageurl

		# all empty if no client param
		self.host = url.netloc
		self.path = url.path
		self.page = url.netloc + url.path

		# assume virthost if not localhost, the domain has a dot, and is not an ip
		self.localhost = url.netloc in ['localhost', '127.0.0.1', '::1']
		self.virthost = not self.localhost and self.host.find('.') > -1 and not self.host.replace('.', '').isdigit()

		self.log('+', 'page: %s' % self.page)

	def watches(self, filename):
		"""Returns whether this client watches this file."""
		if not isinstance(filename, (str, unicode)): return False
		if not filename in self.files:
			# check exact file paths first, then patterns, then cache the result
			patterns = (x for x in itertools.chain(self.files.iteritems(), self.patterns.iteritems()))
			print 'f:', filename
			matches = lambda x: self.file_matches(x, filename)
			self.files[filename] = next(itertools.ifilter(None, itertools.imap(matches, patterns)), None)
			print 'r:', self.files[filename]
		return self.files[filename]

	def watch_files(self, files):
		if not (files and isinstance(files, list)): return
		self.files = dict.fromkeys(files)
		self.update_patterns()

	def file_matches(self, pattern, path):
		"""Returns whether path matches pattern."""

		# path is a full filepath + filename (always with '/' separators)

		# pattern is a tuple of either:
		# (/some/nonmatching/file.html, False) # already checked, return false
		# (/some/matching/file.html, True)     # already matching, return path
		# (/a/resource/url/likethis.css, None) # return if matches path
		# (/regexp/pattern/[\w]+\.html, reobj) # return reobj.match(path)

		if not pattern or not isinstance(pattern, tuple) or len(pattern) != 2: return False
		print pattern
		pattern, value = pattern
		if isinstance(value, bool): return value and pattern
		if value is None: return path.endswith(pattern) and pattern
		try:
			print value.match(path), value.match(path).group(1)
			return value.match(path).group(1)
		except:
			return False

	# make tests
	def update_patterns(self):
		"""
		With a new file list, create a pattern for matching certain files.

		Resource files (css/less/js) can be matched based on their url.
		Types like html or php can be matched if they match the page's url,
		but this is not always the case. (See docs for more on this.)

		The method finds longest common folder between the page's url and
		the resource files, and creates a "common/*.html" -like pattern.
		"""

		# self.files = {
		# 	'/git/sublime-webloader/WebloaderXXXXX/demo/prototype.js': None,
		# 	'/git/sublime-webloader/Webloader/demoXXXXX/webloader.js': None,
		# 	'/git/sublime-webloader/Webloader/demo/sample.less': None,
		# 	'/gitXXXXX/sublime-webloader/Webloader/demo/sample.css': None,
		# 	'/git/sublime-webloaderXXXXX/Webloader/demo/webloader.less': None,
		# 	'/not/relevant/path.css': False,
		# 	'/not/relevant/path.html': True
		# }

		# path = urllib.urlencode({'some': 'ő é í ű'})
		# path = '/git/sublime-webloader/Web loader/ődemo/'
		# path = '/git/sublime-webloader/Web%20loader/%C5%91demo/'
		# path = '/git/sublime-webloader/Webloader/demo/some deeper/page'
		# path = urllib.unquote_plus(path)

		# TODO: refactor/simplify
		def longest_common(path, files, sep='/'):
			path = path.split(sep)
			files = [[k] + k[1:].split(sep) for k, v in files.iteritems() if v is None and k[0] == sep]
			res = None
			for i in xrange(1, len(path)):
				files = [x for x in files if x[i] == path[i]]
				if files: res = files[0], i
				else: break
			if not res: return sep
			return sep + sep.join(res[0][1:res[1] + 1]) + sep

		common = longest_common(self.path, self.files)
		if self.virthost: common = '/' + self.host + common

		# no plugin, no settings, no patterns
		events = self.server and self.server.watch_events()
		if not events or not isinstance(events, dict): return

		extensions = [x.strip() for x in events.keys() \
			if x and not x in self.noregexp_extensions and isinstance(x, (str, unicode))]
		if not extensions: return # nothing configured, beside the excluded ones
		extensions = '|'.join(extensions)
		fullpath = r'.*(%s[\w\._/-]+\.(?:%s))$' % (common, extensions)
		common = '*%s*.(%s)' % (common, extensions)
		self.patterns[common] = re.compile(fullpath)

	def send(self, message):
		sending = super(Client, self).send
		if message.get('filename'):
			print repr(message.filename)
			filename = self.watches(message.filename)
			print repr(filename)
			if isinstance(filename, (str, unicode)): message.filename = filename
			sending(message.pack())

	def on_read(self, message):
		"""Parses client messages; currently only supports the watch and message commands."""

		message = Message(message)
		cmd = message.get('cmd')
		content = message.get('content')

		if cmd == 'watch': self.watch_files(content) # replaces existing watchlist
		elif cmd == 'message': self.server.on_message(self, content)


if __name__ == '__main__':
	Server(debug=3).start()
