#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
Merge molecules by chain.
"""

from ..molecule import Molecule
from ..processors.processor import Processor
from ..log_helpers import StyleAdapter, get_logger
LOGGER = StyleAdapter(get_logger(__name__))


def merge_chains(system, chains, all_chains):
    """
    Merge molecules with the given chains as a single molecule.

    Molecules are merged into the resulting molecule if their chain is in the
    list of chains to merge. The resulting molecule is not connected.

    If a molecule comprises multiple chains, then it is merged only if all the
    chains it comprises are part of the selection.

    The meta variable are not conserved in the process.

    The input system is modified in-place.

    Parameters
    ----------
    system: vermouth.system.System
        The system to modify.
    chains: list[str]
        A container of chain identifier.
    all_chains: bool
        If True, all chains will be merged.
    """
    if not all_chains and len(chains) > 0:
        _chains = set(chains)
    elif all_chains and len(chains) == 0:
        _chains = set()
        for molecule in system.molecules:
            # Molecules can contain multiple chains
            _chains.update(node.get('chain') for node in molecule.nodes.values())
    else:
        raise ValueError("Can specify specific chains or all chains, but not both")

    if any(not c for c in _chains):
        LOGGER.warning('One or more of your chains does not have a chain identifier in input file.')

    merged = Molecule()
    merged._force_field = system.force_field
    has_merged = False
    new_molecules = []
    for molecule in system.molecules:
        molecule_chains = set(node.get('chain') for node in molecule.nodes.values())
        if molecule_chains.issubset(_chains):
            if not has_merged:
                merged.nrexcl = molecule.nrexcl
                new_molecules.append(merged)
            merged.merge_molecule(molecule)
            has_merged = True
        else:
            new_molecules.append(molecule)

    system.molecules = new_molecules


class MergeChains(Processor):
    name = 'MergeChains'

    def __init__(self, chains=None, all_chains=False):
        self.chains = chains or []
        self.all_chains = all_chains

    def run_system(self, system):
        merge_chains(system, self.chains, self.all_chains)
