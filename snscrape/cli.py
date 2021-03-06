import argparse
import contextlib
import datetime
import inspect
import logging
import snscrape.base
import snscrape.modules
import tempfile


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _dump_locals_on_exception():
	try:
		yield
	except Exception as e:
		trace = inspect.trace()
		if len(trace) >= 3:
			with tempfile.NamedTemporaryFile('w', prefix = 'snscrape_locals_', delete = False) as fp:
				for frameRecord in trace[2:]:
					module = inspect.getmodule(frameRecord[0])
					if not module.__name__.startswith('snscrape.') and module.__name__ != 'snscrape':
						continue
					locals_ = frameRecord[0].f_locals
					fp.write(f'Locals from file "{frameRecord.filename}", line {frameRecord.lineno}, in {frameRecord.function}:\n')
					fp.write(repr(locals_))
					fp.write('\n')
					if 'self' in locals_ and hasattr(locals_['self'], '__dict__'):
						fp.write(f'Object dict:\n')
						fp.write(repr(locals_['self'].__dict__))
					fp.write('\n\n')
				logger.fatal(f'Local variables logged to {fp.name}')
		raise


def parse_datetime_arg(arg):
	for format in ('%Y-%m-%d %H:%M:%S %z', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %z', '%Y-%m-%d'):
		try:
			d = datetime.datetime.strptime(arg, format)
		except ValueError:
			continue
		else:
			if d.tzinfo is None:
				return d.replace(tzinfo = datetime.timezone.utc)
			return d
	# Try treating it as a unix timestamp
	try:
		d = datetime.datetime.fromtimestamp(int(arg), datetime.timezone.utc)
	except ValueError:
		pass
	else:
		return d
	raise argparse.ArgumentTypeError(f'Cannot parse {arg!r} into a datetime object')


def parse_args():
	parser = argparse.ArgumentParser(formatter_class = argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('-v', '--verbose', '--verbosity', dest = 'verbosity', action = 'count', default = 0, help = 'Increase output verbosity')
	parser.add_argument('--retry', '--retries', dest = 'retries', type = int, default = 3, metavar = 'N',
		help = 'When the connection fails or the server returns an unexpected response, retry up to N times with an exponential backoff')
	parser.add_argument('-n', '--max-results', dest = 'maxResults', type = int, metavar = 'N', help = 'Only return the first N results')
	parser.add_argument('-f', '--format', dest = 'format', type = str, default = None, help = 'Output format')
	parser.add_argument('--since', type = parse_datetime_arg, metavar = 'DATETIME', help = 'Only return results newer than DATETIME')

	subparsers = parser.add_subparsers(dest = 'scraper', help = 'The scraper you want to use')
	classes = snscrape.base.Scraper.__subclasses__()
	for cls in classes:
		if cls.name is not None:
			subparser = subparsers.add_parser(cls.name, formatter_class = argparse.ArgumentDefaultsHelpFormatter)
			cls.setup_parser(subparser)
			subparser.set_defaults(cls = cls)
		classes.extend(cls.__subclasses__())

	args = parser.parse_args()

	# http://bugs.python.org/issue16308 / https://bugs.python.org/issue26510 (fixed in Python 3.7)
	if not args.scraper:
		raise RuntimeError('Error: no scraper specified')

	return args


def setup_logging(verbosity):
	rootLogger = logging.getLogger()

	# Set level
	if verbosity > 0:
		level = logging.INFO if verbosity == 1 else logging.DEBUG
		rootLogger.setLevel(level)
		for handler in rootLogger.handlers:
			handler.setLevel(level)

	# Create formatter
	formatter = logging.Formatter('{asctime}.{msecs:03.0f}  {levelname}  {name}  {message}', datefmt = '%Y-%m-%d %H:%M:%S', style = '{')

	# Add stream handler
	handler = logging.StreamHandler()
	handler.setFormatter(formatter)
	rootLogger.addHandler(handler)


def main():
	args = parse_args()
	setup_logging(args.verbosity)
	scraper = args.cls.from_args(args)

	i = 0
	with _dump_locals_on_exception():
		for i, item in enumerate(scraper.get_items(), start = 1):
			if args.since is not None and item.date < args.since:
				logger.info(f'Exiting due to reaching older results than {args.since}')
				break
			if args.format is not None:
				print(args.format.format(**item._asdict()))
			else:
				print(item)
			if args.maxResults and i >= args.maxResults:
				logger.info(f'Exiting after {i} results')
				break
		else:
			logger.info(f'Done, found {i} results')
