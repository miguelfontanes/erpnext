# ERPNext - web based ERP (http://erpnext.com)
# Copyright (C) 2012 Web Notes Technologies Pvt Ltd
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import webnotes
from webnotes.utils import nowdate, flt, cstr
from webnotes.model.doctype import get_fields
from webnotes.model.code import get_obj
from webnotes.model.doclist import getlist
from webnotes.model.doc import Document
from utilities.transaction_base import TransactionBase


class AccountsController(TransactionBase):
	def make_gl_entries(self, cancel=False, adv_adj=False, mapper=None, merge_entries=True,
			update_outstanding='Yes', gl_map=None):
		"""make gl entries based on jv, invoice or stock valuation"""
		self.entries = []
		self.merged_entries = []
		self.total_debit = self.total_credit = 0.0
		
		if gl_map:
			self.entries = gl_map
		else:
			self.make_gl_map(mapper)

		self.merge_similar_entries(merge_entries)
		
		self.check_budget(cancel)
		self.save_entries(cancel, adv_adj, update_outstanding)

		if cancel:
			self.set_as_cancel()
		else:
			self.validate_total_debit_credit()
			
			
	def make_gl_map(self, mapper):
		def _gl_map(parent, d, entry_map):
			if self.get_val(entry_map['account'], d, parent) \
					and (self.get_val(entry_map['debit'], d, parent)
			 		or self.get_val(entry_map['credit'], d, parent)):
				gl_dict = {}
				for k in entry_map:
					gl_dict[k] = self.get_val(entry_map[k], d, parent)
				self.entries.append(gl_dict)
			
		# get entries
		gl_fields = ", ".join(get_fields("GL Mapper Detail"))
		entry_map_list = webnotes.conn.sql("""select %s from `tabGL Mapper Detail` 
			where parent = %s""" % (gl_fields, '%s'), mapper or self.doc.doctype, as_dict=1)
		
		for entry_map in entry_map_list:
			table_field = entry_map.get("table_field")
			
			# table_field does not exist in gl entry table
			entry_map.pop("table_field")
			
			if table_field:
				for d in getlist(self.doclist, table_field):
					# purchase_tax_details is the table of other charges in purchase cycle
					if table_field == "purchase_tax_details" and \
							d.fields.get("category") == "Valuation":
						# don't create gl entry for only valuation type charges
						continue
					_gl_map(self.doc, d, entry_map)
			else:
				_gl_map(None, self.doc, entry_map)
			
				
	def get_val(self, src, d, parent=None):
		"""Get field values from the voucher"""
		if not src:
			return None
		if src.startswith('parent:'):
			return parent.fields[src.split(':')[1]]
		elif src.startswith('value:'):
			return eval(src.split(':')[1])
		elif src:
			return d.fields.get(src)
				
				
	def merge_similar_entries(self, merge_entries):
		if merge_entries:
			for entry in self.entries:
				# if there is already an entry in this account then just add it to that entry
				same_head = self.check_if_in_list(entry)
				if same_head:
					same_head['debit']	= flt(same_head['debit']) + flt(entry['debit'])
					same_head['credit'] = flt(same_head['credit']) + flt(entry['credit'])
				else:
					self.merged_entries.append(entry)
		else:
			self.merged_entries = self.entries

	
	def save_entries(self, cancel, adv_adj, update_outstanding):
		def _swap(gle):
			gle.debit, gle.credit = abs(flt(gle.credit)), abs(flt(gle.debit))
		
		for entry in self.merged_entries:
			gle = Document('GL Entry', fielddata=entry)
			
			# toggle debit, credit if negative entry
			if flt(gle.debit) < 0 or flt(gle.credit) < 0:
				_swap(gle)

			# toggled debit/credit in two separate condition because both should be executed at the 
			# time of cancellation when there is negative amount (tax discount)
			if cancel:
				_swap(gle)

			gle_obj = get_obj(doc=gle)
			# validate except on_cancel
			if not cancel:
				gle_obj.validate()

			# save
			gle.save(1)
			gle_obj.on_update(adv_adj, cancel, update_outstanding)

			# update total debit / credit
			self.total_debit += flt(gle.debit)
			self.total_credit += flt(gle.credit)


	def check_if_in_list(self, gle):
		for e in self.merged_entries:
			if e['account'] == gle['account'] and \
					cstr(e['against_voucher']) == cstr(gle['against_voucher']) and \
					cstr(e['against_voucher_type']) == cstr(gle['against_voucher_type']) and \
					cstr(e['cost_center']) == cstr(gle['cost_center']):
				return e
			
	def validate_total_debit_credit(self):
		if abs(self.total_debit - self.total_credit) > 0.005:
			msgprint("""Debit and Credit not equal for this voucher: Diff (Debit) is %s""" %
			 	(self.total_debit - self.total_credit), raise_exception=1)
		
	def set_as_cancel(self):
		webnotes.conn.sql("""update `tabGL Entry` set is_cancelled='Yes' 
			where voucher_type=%s and voucher_no=%s""", (self.doc.doctype, self.doc.name))
		
	def check_budget(self, cancel):
		for gle in self.merged_entries:
			if gle['cost_center']:
				#check budget only if account is expense account
				acc_details = webnotes.conn.get_value("Account", gle['account'], 
					['is_pl_account', 'debit_or_credit'])
			
				if acc_details[0]=="Yes" and acc_details[1]=="Debit":
					get_obj('Budget Control').check_budget(gle, cancel)