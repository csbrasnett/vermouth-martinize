# Copyright 2018 University of Groningen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Handle the RTP format from Gromacs.
"""

import collections
import copy
import networkx as nx

from ..molecule import Block, Link, Interaction

from ..log_helpers import StyleAdapter, get_logger
LOGGER = StyleAdapter(get_logger(__name__))

__all__ = ['read_rtp']

# Name of the subsections in RTP files.
# Names starting with a '_' are for internal use.
RTP_SUBSECTIONS = ('atoms', 'bonds', 'angles', 'dihedrals',
                   'impropers', 'cmap', 'exclusions',
                   '_bondedtypes')


_BondedTypes = collections.namedtuple(
    '_BondedTypes',
    'bonds angles dihedrals impropers all_dihedrals nrexcl HH14 remove_dih'
)


class _IterRTPSubsectionLines:
    """
    Iterate over the lines of an RTP file within a subsection.
    """

    def __init__(self, parent):
        self.parent = parent
        self.lines = parent.lines
        self.running = True

    def __next__(self):
        if not self.running:
            raise StopIteration
        line = next(self.lines)
        if line.strip().startswith('['):
            self.parent.buffer.append(line)
            self.running = False
            raise StopIteration
        return line

    def __iter__(self):
        return self

    def flush(self):
        """
        Move the iterator after the last line of the subsection.
        """
        for _ in self:
            pass


class _IterRTPSubsections:
    """
    Iterate over the subsection of a RTP file within a section.

    For each subsections, yields its name and  an iterator over its lines.
    """

    def __init__(self, parent):
        self.parent = parent
        self.lines = parent.lines
        self.buffer = collections.deque()
        self.current_subsection = None
        self.running = True

    def __next__(self):
        if not self.running:
            raise StopIteration
        if self.current_subsection is not None:
            self.current_subsection.flush()
        if self.buffer:
            line = self.buffer.popleft()
        else:
            line = next(self.lines)
        stripped = line.strip()
        if stripped.startswith('['):
            # A section header looks like "[ name ]". It matches the following
            # regexp: r"^\s*\[\s*(?P<name>)\s*\]\s*$". Once stripped, the
            # trailing spaces at the beginning and at the end are removed so
            # the string starts with '[' and ends with ']'. The slicing remove
            # these brackets. The final call to strip remove the potential
            # white characters between the brackets and the section name.
            name = stripped[1:-1].strip()
            if name in RTP_SUBSECTIONS:
                subsection = _IterRTPSubsectionLines(self)
                self.current_subsection = subsection
                return name, subsection
            self.parent.buffer.append(line)
            self.running = False
            raise StopIteration
        raise IOError('I am almost sure I should not be here...')

    def __iter__(self):
        return self

    def flush(self):
        """
        Move the iterator after the last subsection of the section.
        """
        for _ in self:
            pass


class _IterRTPSections:
    """
    Iterate over the sections of a RTP file.

    For each section, yields the name of the sections and an iterator over its
    subsections.
    """

    def __init__(self, lines):
        self.lines = lines
        self.buffer = collections.deque()
        self.current_section = None

    def __next__(self):
        if self.current_section is not None:
            self.current_section.flush()
        if self.buffer:
            line = self.buffer.popleft()
        else:
            line = next(self.lines)
        stripped = line.strip()
        if stripped.startswith('['):
            name = stripped[1:-1].strip()
            section = _IterRTPSubsections(self)
            self.current_section = section
            # The "[ bondedtypes ]" is special in the sense that it does
            # not have subsection, but instead have its content directly
            # under it. This breaks the neat hierarchy the rest of the file
            # has. Here we restore the hierarchy by faking that the file
            # contains:
            #
            #    [ bondedtypes ]
            #     [ _bondedtypes ]
            #
            # For now, I do that on the basis of the section name. If other
            # sections are special that way, I'll detect them with a look
            # ahead.
            if name == 'bondedtypes':
                section.buffer.append(' [ _bondedtypes ]')
            return name, section
        raise IOError('Hum... There is a bug in the RTP reader.')

    def __iter__(self):
        return self


def _atoms(subsection, block):
    for line in subsection:
        name, atype, charge, charge_group = line.split()
        atom = {
            'atomname': name,
            'atype': atype,
            'charge': float(charge),
            'charge_group': int(charge_group),
        }
        block.add_atom(atom)


def _base_rtp_parser(interaction_name, natoms):
    def wrapped(subsection, block):
        """
        Parse the lines from a RTP subsection and populate the block.
        """
        interactions = []
        for line in subsection:
            splitted = line.strip().split()
            atoms = splitted[:natoms]
            parameters = splitted[natoms:]
            interactions.append(Interaction(atoms=atoms,
                                            parameters=parameters,
                                            meta={}))
            block.add_nodes_from(atoms)
        block.interactions[interaction_name] = interactions
    return wrapped


def _parse_bondedtypes(section):
    # Default taken from
    # 'src/gromacs/gmxpreprocess/resall.cpp::read_resall' in the Gromacs
    # source code.

    defaults = _BondedTypes(bonds=1, angles=1, dihedrals=1,
                            impropers=1, all_dihedrals=0,
                            nrexcl=3, HH14=1, remove_dih=1)

    # The 'bondedtypes' section contains its line directly under it. In
    # order to match the hierarchy model of the rest of the file, the
    # iterator actually yields a subsection named '_bondedtypes'. We need
    # to read the fist line of that first virtual subsection.
    _, lines = next(section)
    line = next(lines)
    read = [int(x) for x in line.split()]

    # Fill with the defaults. The file gives the values in order so we
    # need to append the missing values from the default at the end.
    bondedtypes = _BondedTypes(*(read + list(defaults[len(read):])))

    # Make sure there is no unexpected lines in the section.
    # Come on Jonathan! There must be a more compact way of doing it.
    try:
        next(lines)
    except StopIteration:
        pass
    else:
        raise IOError('"[ bondedtypes ]" section is missformated.')
    try:
        next(section)
    except StopIteration:
        pass
    else:
        raise IOError('"[ bondedtypes ]" section is missformated.')
    return bondedtypes


def _complete_block(block, bondedtypes):
    """
    Add information from the bondedtypes section to a block.

    Generate implicit dihedral angles, and add function types to the
    interactions.
    """
    block.make_edges_from_interactions()

    # Add function types to the interaction parameters. This is done as a
    # post processing step to cluster as much interaction specific code
    # into this method.
    # I am not sure the function type can be set explicitly in the RTP
    # file except through the bondedtypes section. If it is possible, then the
    # following code can break and atoms can have the function type written
    # twice. Yet, none of the RTP files distributed with Gromacs 2016.3 causes
    # issue.
    functypes = {
        'bonds': bondedtypes.bonds,
        'angles': bondedtypes.angles,
        'dihedrals': bondedtypes.dihedrals,
        'impropers': bondedtypes.impropers,
        'exclusions': 1,
        'cmap': 1,
    }
    for functype in functypes:
        for interaction in block.interactions[functype]:
            interaction.parameters.insert(0, functypes[functype])

    # Set the nrexcl to the block.
    block.nrexcl = bondedtypes.nrexcl


def _split_blocks_and_links(pre_blocks):
    """
    Split all the pre-blocks from `pre_block` into blocks and links.

    Parameters
    ----------
    pre_blocks: dict
        A dict with residue names as keys and instances of :class:`Block`
        as values.

    Returns
    -------
    blocks: dict
        A dict like `pre_block` with all inter-residues information
        stripped out.
    links: list
        A list of instances of :class:`Link` containing the inter-residues
        information from `pre_blocks`.

    See Also
    --------
    _split_block_and_link
        Split an individual pre-block into a block and a link.
    """
    blocks = {}
    all_links = []
    for name, pre_block in pre_blocks.items():
        block, links = _split_block_and_link(pre_block)
        blocks[name] = block
        if links:
            for link in links:
                if link:
                    all_links.append(link)
    return blocks, all_links


def _split_block_and_link(pre_block):
    """
    Split `pre_block` into a block and a link.

    A pre-block is a block as read from an RTP file. It contains both
    intra-residue and inter-residues information. This method split this
    information so that the intra-residue interactions are put in a block
    and the inter-residues ones are put in a link.

    Parameters
    ----------
    pre_block: Block
        The block to split.

    Returns
    -------
    block: Block
        All the intra-residue information.
    links: List[Link]
        All the inter-residues information.
    """
    block = Block(force_field=pre_block.force_field)
    link = Link()

    # It is easier to fill the interactions using a defaultdict,
    # yet defaultdicts are more annoying when reading as querying them
    # creates the keys. So the interactions are revert back to regular
    # dict at the end.
    block.interactions = collections.defaultdict(list)
    link.interactions = collections.defaultdict(list)

    block.name = pre_block.name
    try:
        block.nrexcl = pre_block.nrexcl
    except AttributeError:
        pass

    links = []

    for interaction_type, interactions in pre_block.interactions.items():
        for interaction in interactions:
            # Atoms that are not defined in the atoms section but only in interactions
            # (because they're part of the neighbours), don't have an atomname
            atomnames = [pre_block.nodes[n_idx].get('atomname') or n_idx for n_idx in interaction.atoms]
            if any(atomname[0] in '+-' for atomname in atomnames):
                link = Link(pre_block.subgraph(interaction.atoms))
                if not nx.is_connected(link):
                    LOGGER.info('Discarding {} between atoms {} for link'
                                ' defined in residue {} in force field {}'
                                ' because it is disconnected.',
                                interaction_type, ' '.join(interaction.atoms),
                                pre_block.name, pre_block.force_field.name,
                                type='inconsistent-data')
                    continue
                links.append(link)

    block = pre_block
    block.remove_nodes_from([n for n in pre_block if pre_block.nodes[n].get('atomname', n)[0] in '+-'])
    for node in block:
        block.nodes[node]['resname'] = block.name

    # Atoms from a links are matched against a molecule based on its node
    # attributes. The name is a primary criterion, but other criteria can be
    # the residue name (`resname` key) or the residue order in the chain
    # (`order` key). The residue order is 0 when refering to the current
    # residue, +int to refer to residues after the current one in the sequence,
    # -int to refer to a previous residue in the sequence, and '*' for any
    # residue but the current one.
    # RTP files convey the order by prefixing the names with + or -. We need to
    # get rid of these prefixes.
    order = {'+': +1, '-': -1}

    for link in links:
        relabel_mapping = {}
        # (Deep)copy interactions, since relabel_nodes removes/adds nodes, which
        # wipes out all interactions otherwise.
        interactions = copy.deepcopy(link.interactions)
        remove_attrs = 'atype charge charge_group'.split()
        for idx, node in enumerate(link.nodes()):
            atomname = node
            if node[0] in '+-':
                link.nodes[node]['order'] = order[node[0]]
                atomname = atomname[1:]
            else:
                link.nodes[node]['order'] = 0
                link.nodes[node]['resname'] = block.name
            for attr in remove_attrs:
                if attr in link.nodes[node]:
                    del link.nodes[node][attr]
            link.nodes[node]['atomname'] = atomname
            relabel_mapping[node] = idx
        nx.relabel_nodes(link, relabel_mapping, copy=False)

        # By relabelling the nodes, we lost the relations between interactions and
        # nodes, so we need to relabel the atoms in the interactions
        new_interactions = collections.defaultdict(list)
        for name, interactions in interactions.items():
            for interaction in interactions:
                atoms = tuple(relabel_mapping[atom] for atom in interaction.atoms)
                if not any(link.nodes[node]['order'] for node in atoms):
                    continue
                new_interactions[name].append(Interaction(
                    atoms=atoms,
                    parameters=interaction.parameters,
                    meta=interaction.meta
                ))
        link.interactions = new_interactions
        link.interactions = dict(link.interactions)

    # Revert the interactions back to regular dicts to avoid creating
    # keys when querying them.
    block.interactions = dict(block.interactions)
    return block, links


def _clean_lines(lines):
    # TODO: merge continuation lines
    for line in lines:
        splitted = line.split(';', 1)
        if splitted[0].strip():
            yield splitted[0]


def read_rtp(lines, force_field):
    """
    Read blocks and links from a Gromacs RTP file to populate a force field

    Parameters
    ----------
    lines: collections.abc.Iterator
        An iterator over the lines of a RTP file (e.g. a file handle, or a
        list of string).
    force_field: vermouth.forcefield.ForceField
        The force field to populate in place.

    Raises
    ------
    IOError
        Something in the file could not be parsed.
    """
    _subsection_parsers = {
        'atoms': _atoms,
        'bonds': _base_rtp_parser('bonds', natoms=2),
        'angles': _base_rtp_parser('angles', natoms=3),
        'impropers': _base_rtp_parser('impropers', natoms=4),
        'cmap': _base_rtp_parser('cmap', natoms=5),
    }
    # An RTP file contains both the blocks and the links.
    # We first read everything in "pre-blocks"; then we separate the
    # blocks from the links.
    pre_blocks = {}
    bondedtypes = None
    cleaned = _clean_lines(lines)
    for section_name, section in _IterRTPSections(cleaned):
        if section_name == 'bondedtypes':
            bondedtypes = _parse_bondedtypes(section)
            continue
        block = Block(force_field=force_field)
        pre_blocks[section_name] = block
        block.name = section_name

        for subsection_name, subsection in section:
            if subsection_name in _subsection_parsers:
                _subsection_parsers[subsection_name](subsection, block)

    # Pre-blocks only contain the interactions that are explicitly
    # written in the file. Some are incomplete (missing implicit defaults)
    # or must be built from the "bondedtypes" rules.
    for pre_block in pre_blocks.values():
        _complete_block(pre_block, bondedtypes)

    # At this point, the pre-blocks contain both the intra- and
    # inter-residues information. We need to split the pre-blocks into
    # blocks and links.
    blocks, links = _split_blocks_and_links(pre_blocks)

    force_field.blocks.update(blocks)
    force_field.links.extend(links)
    force_field.variables['bondedtypes'] = bondedtypes
