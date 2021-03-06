import time
import ConfigParser
import logging

import requests
import libtorrent as lt
from jenkinsapi.jenkins import Jenkins


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
fh = logging.FileHandler('grabber.log')
LOG.addHandler(fh)


class IsoSearchCore():
    def __init__(self):
        self.config = ConfigParser.RawConfigParser(allow_no_value=True)
        self.config.read('config.conf')
        self.fuel_version = self.config.get("jenkins", "fuel_version")
        self.jenkins_url = self.config.get("jenkins", "url")
        self.jenkins = Jenkins(self.jenkins_url, username=None,
                               password=None)
        self.job_name = '{0}.all'.format(self.fuel_version)
        self.job = self.jenkins[self.job_name]
        self.stable_iso_number = self.find_correct_iso()
        self.stable_iso_build = self.job.get_build(self.stable_iso_number)

    def get_downstream_job_list(self):
        return self.job.get_downstream_job_names()

    def find_correct_iso(self):
        last_good_build = self.job.get_last_good_build()
        mark_stable = False
        LOG.debug('Starting...')
        while mark_stable is False:

            last_good_build_number = last_good_build.get_number()
            LOG.debug('Try build: {0}'.format(last_good_build))
            LOG.debug('------------------')
            downstream_job_list = self.get_downstream_job_list()
            LOG.debug('Got downstream jobs: {0}'.format(downstream_job_list))

            for job in downstream_job_list:
                last_job_build = self.jenkins[job].get_last_build()
                LOG.debug('******************')
                LOG.debug('Checking downstream job {0}'.format(job))

                while (last_job_build.get_upstream_build_number()
                       != last_good_build.get_number() or
                       last_job_build.get_upstream_job_name() !=
                       self.job_name):

                    if last_job_build.get_upstream_job_name() != self.job_name:
                        LOG.debug('Skipped: {0}'.format(last_job_build))
                        last_number = last_job_build.get_number() - 1
                        last_job_build = self.jenkins[job].get_build(
                            last_number)

                    if last_job_build.get_upstream_build_number() is None:
                        last_number = last_job_build.get_number() - 1
                        last_job_build = self.jenkins[job].get_build(
                            last_number)
                        LOG.debug('Skipped: {0}'.format(last_job_build))

                    while (last_job_build.get_upstream_job_name() ==
                           self.job_name):

                        if last_job_build.get_upstream_job_name() is None:
                            last_number = last_job_build.get_number() - 1
                            last_job_build = self.jenkins[job].get_build(
                                last_number)
                            LOG.debug('Skipped: {0}'.format(last_job_build))
                            break

                        if (last_job_build.get_upstream_build_number() ==
                                last_good_build.get_number()):
                            LOG.debug('Found: {0} at {1}'
                                      .format(last_good_build, last_job_build))
                            break

                        last_number = last_job_build.get_number() - 1
                        last_job_build = self.jenkins[job].get_build(
                            last_number)

                if last_job_build.get_status() == 'SUCCESS':
                    LOG.debug('Test downstream job build passed')
                    mark_stable = True
                else:
                    LOG.warning('Test downstream job build failed')
                    next_build_number = last_good_build_number - 1
                    last_good_build = self.job.get_build(next_build_number)
                    while last_good_build.get_status() != 'SUCCESS':
                        next_build_number -= 1
                        last_good_build = self.job.get_build(next_build_number)
                    LOG.debug('Now checking iso#{0}'.format(next_build_number))
                    mark_stable = False
                    break
        LOG.debug('Stable ISO found: {0}'.format(last_good_build))
        return last_good_build.get_number()

    def get_magnet_link(self):
        magnet = requests.get('{0}view/{1}/job/{2}/{3}/artifact/magnet_link.txt'
                              .format(self.jenkins_url, self.fuel_version,
                                      self.job_name, self.stable_iso_number))
        magnet = magnet.text.encode('ascii').strip().strip('MAGNET_LINK=')
        return magnet


class IsoGrabberCore(IsoSearchCore):
    def __init__(self):
        IsoSearchCore.__init__(self)
        self.magnet_link = self.get_magnet_link()
        self.path = self.config.get("storage", "store_path")
        self.jenkins_url = self.config.get("jenkins", "url")

    def download_iso(self):
        session = lt.session()
        session.listen_on(6881, 6891)
        params = {
            'save_path': self.path,
            'storage_mode': lt.storage_mode_t(2),
            'paused': False,
            'auto_managed': True,
            'duplicate_is_error': True}
        handle = lt.add_magnet_uri(session, self.magnet_link, params)
        session.start_dht()

        LOG.debug(
            'Downloading metadata for build {0}'.format(self.stable_iso_build))
        while not handle.has_metadata():
            time.sleep(1)
        LOG.debug('Got metadata, starting torrent download...')
        while handle.status().state != lt.torrent_status.seeding:
            status = handle.status()
            state_str = ['queued', 'checking', 'downloading metadata',
                         'downloading',
                         'finished', 'seeding', 'allocating']
            LOG.debug('{0:.2f}% complete (down: {1:.1f} kb/s up: {2:.1f} kB/s '
                      'peers: {3:d}) {4:s} {5:d}.3'
                      .format(status.progress * 100,
                              status.download_rate / 1000,
                              status.upload_rate / 1000,
                              status.num_peers,
                              state_str[status.state],
                              status.total_download / 1000000))
            time.sleep(5)
        LOG.debug('Ready for deploy iso {0}'.format(self.stable_iso_build))