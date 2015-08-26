#!/usr/bin/env python
"""
:mod:`ppmac.completer` -- Ppmac Completer
=========================================

.. module:: ppmac.completer
   :synopsis: Allows for Python introspection into Power PMAC variables.
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""
# this whole module should be redone if I ever get a chance...

from __future__ import print_function
import os
import re
import sys
import subprocess

import sqlite3 as sqlite

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))


def get_index(name):
    m = re.search('\[(\d+)\]', name)
    if m:
        return int(m.groups()[0])
    return None


def remove_indices_and_brackets(name):
    return re.sub('(\[\d+\]?)', '', name)


def remove_indices(name):
    return re.sub('(\[\d+\])', '[]', name)


def fix_name(name):
    return name.replace('[]', '')


def check_alias(c, name):
    c.execute('select Alias from software_tbl0 where Command=? collate nocase', (name, ))
    try:
        row = c.fetchone()
        alias = row['Alias']
        if alias is not None:
            return alias
    except:
        pass

    return name


class PPCompleterNode(object):
    def __init__(self, conn, parent, row, index=None, gpascii=None):
        self.gpascii = gpascii
        self.conn = conn
        self.row = row
        self.index = index

        self._name = row['Command']
        self.parent = parent
        self._cache = {}

        c = conn.cursor()

        top = self.full_name.split('.')[0]
        top = check_alias(c, top)
        if not self.parent:
            cid = top
        else:
            cid = self._name

        cid = remove_indices(cid)
        c.execute('select * from software_tbl1 where CommandID=?', (cid, ))
        self.info = dict((fix_name(item['Command']), item) for item in c.fetchall())

        #print('gatechan', top, 'cid', self._db_name)
        c.execute('select * from software_tbl2 where GateChan=? and CommandID=?', (top, self._db_name))
        gate_items = dict((fix_name(item['Command']), item) for item in c.fetchall())
        self.info.update(gate_items)

        if not gate_items:
            c.execute('select * from software_tbl2 where CommandID=?', (cid, ))
            table2_items = dict((fix_name(item['Command']), item) for item in c.fetchall())
            self.info.update(table2_items)

        self._lower_case = dict((name.lower(), name) for name in self.info.keys())
        self._set_docstring()

    def search(self, text, search_row=True, case_insensitive=True):
        """
        Search keys and optionally all rows for `text`
        Returns dictionary of {key: rows}
        """
        ret = {}
        if case_insensitive:
            text = text.lower()

        for key, info in self.info.items():
            match = False
            if text in key:
                match = True
            elif case_insensitive and text in key.lower():
                match = True
            elif search_row:
                s = str(info)
                if case_insensitive:
                    s = s.lower()
                match = (text in s)

            if match:
                ret[key] = self.info[key]

        return ret

    def _set_docstring(self):
        info_keys = ['Comments', 'AddedComments', 'TypeInfo',
                     'RangeInfo', 'Units', 'DefaultInfo',
                     'UserLevel', 'Category']

        doc = []
        for key in info_keys:
            if key in self.row:
                value = self.row[key]
                if value is None:
                    continue

                if key == 'AddedComments':
                    key = 'Comments'

                doc.append((key, value))

        def fix_desc(s):
            return str(s).replace('NULL', 'None')

        doc = ['%s: %s' % (name, fix_desc(desc)) for name, desc in doc]
        self.__doc__ = '\n'.join(doc)

    def __dir__(self):
        return self.info.keys()

    def _get_node(self, row):
        full_name = row['Command']
        try:
            return self._cache[full_name]
        except KeyError:
            if full_name.endswith('[]'):
                node = PPCompleterList(self.conn, self.full_name, row,
                                       gpascii=self.gpascii)
            else:
                node = PPCompleterNode(self.conn, self.full_name, row,
                                       gpascii=self.gpascii)

            self._cache[full_name] = node
            return node

    def __getattr__(self, key):
        try:
            key = key.lower()
            if key in self._lower_case:
                key = self._lower_case[key]

            return self._get_node(self.info[key])
        except KeyError:
            raise AttributeError('%s.%s' % (str(self), key))

    @property
    def name(self):
        if self.index is not None:
            return '%s[%d]' % (self._name[:-2], self.index)
        else:
            return self._name

    @property
    def full_name(self):
        if self.parent:
            return '%s.%s' % (self.parent, self.name)
        else:
            return self.name

    @property
    def address(self):
        return '%s.a' % (self.full_name, )

    @property
    def _db_name(self):
        return remove_indices(self._name)

    @property
    def _db_full_name(self):
        return remove_indices(self.full_name)

    def __str__(self):
        return self.full_name

    @property
    def value(self):
        if self.gpascii is not None:
            return self.gpascii.get_variable(self.full_name)
        else:
            return None

    __repr__ = __str__


class PPCompleterList(object):
    def __init__(self, conn, parent, row, gpascii=None):
        self.gpascii = gpascii
        self.conn = conn
        self.row = row
        self.name = row['Command']
        self.parent = parent
        self.item0 = PPCompleterNode(self.conn, self.parent, self.row, index=0,
                                     gpascii=gpascii)
        self.items = {0: self.item0}

    @property
    def full_name(self):
        if self.parent:
            return '%s.%s' % (self.parent, self.name)
        else:
            return self.name

    def __getitem__(self, idx):
        try:
            return self.items[idx]
        except KeyError:
            node = PPCompleterNode(self.conn, self.parent, self.row, index=idx,
                                   gpascii=self.gpascii)
            self.items[idx] = node
            return node

    def search(self, *args, **kwargs):
        return self.item0.search(*args, **kwargs)

    def __getattr__(self, key):
        if hasattr(self.item0, key):
            return getattr(self.item0, key)
        raise AttributeError(key)

    def __dir__(self):
        return dir(self.item0)

    def __str__(self):
        return self.full_name

    __repr__ = __str__


class PPCompleter(object):
    def __init__(self, conn, gpascii=None):
        self.conn = conn
        self.gpascii = gpascii

        tbl0 = conn.cursor()
        tbl0.execute('select * from software_tbl0')
        rows = tbl0.fetchall()
        self.top_level = dict((fix_name(item['Command']), item) for item in rows)
        self._lower_case = dict((name.lower(), name) for name in self.top_level.keys())
        self._cache = {}

    def __dir__(self):
        return self.top_level.keys()

    def _get_node(self, row):
        full_name = row['Command']
        try:
            return self._cache[full_name]
        except KeyError:
            if full_name.endswith('[]'):
                node = PPCompleterList(self.conn, '', row, gpascii=self.gpascii)
            else:
                node = PPCompleterNode(self.conn, '', row, gpascii=self.gpascii)

            self._cache[full_name] = node
            return node

    def __getattr__(self, name):
        name = name.lower()
        if name in self._lower_case:
            name = self._lower_case[name]

            return self._get_node(self.top_level[name])

        raise AttributeError(name)

    def check(self, addr):
        """
        Check a PPMAC variable and fix its case, if necessary
        Returns the variable with proper case or raises AttributeError
        on failure.
        """
        #print('-- check', addr)
        addr = addr.split('.')
        obj = self
        for i, entry in enumerate(addr):
            index = get_index(entry)

            entry = remove_indices(entry)
            if entry.endswith('[]'):
                entry = entry[:-2]

            try:
                obj = getattr(obj, entry)
            except AttributeError as ex:
                if i > 0:
                    raise AttributeError('%s does not exist in %s' %
                                         (entry, '.'.join(addr[:i]))
                                         )
                else:
                    raise AttributeError('%s does not exist' % (entry))

            if index is not None and not isinstance(obj, PPCompleterList):
                raise AttributeError('%s is not a list' % (entry))
            elif index is None and isinstance(obj, PPCompleterList):
                raise AttributeError('%s is a list' % (entry))

            if index is not None:
                obj = obj[index]

            name = obj.name
            addr[i] = name

        return obj


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def start_completer_from_db(dbfile=':memory:', gpascii=None):
    conn = sqlite.connect(dbfile)
    conn.row_factory = dict_factory
    return PPCompleter(conn, gpascii=gpascii)


def start_completer_from_sql_script(script, db_file, gpascii=None):
    conn = sqlite.connect(db_file)
    conn.row_factory = dict_factory

    c = conn.cursor()
    c.executescript(script)
    conn.commit()
    return PPCompleter(conn, gpascii=gpascii)


def start_completer_from_sql_file(sql_file='ppmac.sql', db_file=':memory:'):
    with open(sql_file, 'rt') as f:
        sql = f.read()
    return start_completer_from_sql_script(sql, db_file)


def start_completer_from_mysql(mysql_host, ppmac_ip, mysql_user='root',
                               script='mysql2sqlite.sh', db_file=':memory:',
                               gpascii=None):
    """
    database is 'ppmac' + ip address with dots as underscores:
    so 10.0.0.98 -> ppmac10_0_0_98

    on windows machine:
     mysql -u root
     GRANT ALL PRIVILEGES ON ppmac10_0_0_98.* TO 'root'@'%';
    edit C:\Program Files\MySQL\MySQL Server 5.0
    from:
     host = localhost
    to:
     host = 10.0.0.6 <-- windows machine ip accessible by this script

    net stop mysql
    net start mysql

    then finally:

    sh ./mysql2sqlite.sh -h 10.0.0.6 -u root ppmac10_0_0_98 > temp.sql
    iconv -f latin1 -t utf-8 temp.sql > ppmac.sql
    """
    dbname = 'ppmac%s' % (ppmac_ip.replace('.', '_'))

    script = os.path.join(MODULE_PATH, script)
    cmd = 'sh %(script)s -h %(mysql_host)s -u %(mysql_user)s %(dbname)s | iconv -f latin1 -t utf-8'
    cmd = cmd % locals()

    print('Executing', cmd)
    try:
        sqlite_sql = subprocess.check_output(cmd, shell=True)
    except subprocess.CalledProcessError as ex:
        print('Failed (ret=%d): %s' % (ex.returncode, ex.output))
        return None

    return start_completer_from_sql_script(sqlite_sql, db_file, gpascii=gpascii)


def main(ppmac_ip='10.0.0.98', windows_ip='10.0.0.6'):
    db_file = os.path.join(MODULE_PATH, 'ppmac.db')
    c = None
    if os.path.exists(db_file):
        try:
            c = start_completer_from_db(db_file)
        except Exception as ex:
            print('Unable to load current db file: %s (%s) %s' %
                  (db_file, ex.__class__.__name__, ex))

    if c is None:
        if os.path.exists(db_file):
            os.unlink(db_file)
        c = start_completer_from_mysql(windows_ip, ppmac_ip, db_file=db_file,
                                       gpascii=None)

    if c is None:
        sys.exit(1)

    #print(dir(c))
    print(c.Sys)
    print(c.Gate3)
    print(c.Gate3[0])
    print(c.Gate3[0].Chan[0])
    print(c.Gate3[0].Chan[0].ABC)
    print(c.Acc24E3[0])
    print(c.Acc24E3[0].Chan[0])
    print()
    print('docstring:')
    print(c.Gate3[0].Chan[0].ABC.__doc__)
    print('check', c.check('Gate3[0].Chan[0].ABC'))
    print('check', c.check('Sys'))
    print('check', c.check('Gate3[0]'))
    print('check', c.check('acc24e3[0].chan[0]'))
    print(c.Motor[3])
    print('check', c.check('motor[3].pos'))

    try:
        print(c.check('Acc24E3[0].Chan[0].blah'))
    except AttributeError as ex:
        print('ok -', ex)
    else:
        print('fail')

    try:
        print(c.check('Acc24E3.Chan.ABC'))
    except AttributeError as ex:
        print('ok -', ex)
    else:
        print('fail')

    try:
        print(c.check('Acc24E3[0].Chan[0].ABC[0]'))
    except AttributeError as ex:
        print('ok -', ex)
    else:
        print('fail')

    c0 = c.acc24e3[0].chan
    for key, value in c0.search('4095').items():
        print(key, value)

    c0 = c.acc24e3[0].chan[0]
    for key, value in c0.search('4095').items():
        print(key, value.values())

    print(dir(c.acc24e2s[4].chan[0]))
    try:
        c.acc24e2s[4].chan[0].pfmwidth
    except AttributeError as ex:
        print('ok -', ex)
    else:
        print('fail')

    print(c.check('acc24e2s[4].chan[0]'))


if __name__ == '__main__':
    main()
