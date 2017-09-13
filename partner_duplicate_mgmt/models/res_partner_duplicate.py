# -*- coding: utf-8 -*-
# © 2017 Savoir-faire Linux
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartnerDuplicate(models.Model):

    _name = 'res.partner.duplicate'
    _description = 'Partner Duplicate'
    _inherit = ['mail.thread']

    partner_1_id = fields.Many2one(
        'res.partner', string='Partner 1', readonly=True)
    partner_2_id = fields.Many2one(
        'res.partner', string='Partner 2', readonly=True)
    partner_preserved_id = fields.Many2one(
        'res.partner', string='Partner Preserved',
        track_visibility='onchange')
    merge_line_ids = fields.One2many(
        'res.partner.merge.line', 'duplicate_id', string='Merge Lines')
    state = fields.Selection(
        string='State',
        selection=[
            ('to_validate', 'To Validate'),
            ('resolved', 'Resolved (Not Duplicate)'),
            ('merged', 'Merged'),
        ], default='to_validate',
        track_visibility='onchange',
    )

    @api.onchange('partner_preserved_id')
    def onchange_partner_preserved_id(self):
        if self.partner_preserved_id:
            partner_1_preserved = (
                self.partner_preserved_id == self.partner_1_id)
            for line in self.merge_line_ids:
                line.partner_1_selected = partner_1_preserved
                line.partner_2_selected = not partner_1_preserved

    def update_preserved_partner(self):
        vals = {}
        for line in self.merge_line_ids:
            field_name = line.duplicate_field_id.technical_name
            field_value = False

            if (
                self.partner_preserved_id == self.partner_1_id and
                line.partner_2_selected
            ):
                field_value = line.partner_2_value

            if (
                self.partner_preserved_id == self.partner_2_id and
                line.partner_1_selected
            ):
                field_value = line.partner_1_value

            if field_value:
                if line.duplicate_field_id.type == 'many2one':
                    field_value = getattr(self.partner_2_id, field_name).id
                vals[field_name] = field_value

        self.partner_preserved_id.write(vals)

    def merge_partners(self):
        # Verify that a partner is checked for all lines
        for line in self.merge_line_ids:
            if not line.partner_1_selected and not line.partner_2_selected:
                raise UserError(_(
                    'Please select a value to preserve for the field %s.') %
                    (line.duplicate_field_id.name))

        # Update preserved partner fields values
        self.update_preserved_partner()

        # Call the method _merge of the crm partner merge widget
        partners = self.partner_1_id | self.partner_2_id
        base_wizard = self.env['base.partner.merge.automatic.wizard']
        base_wizard.with_context(do_not_unlink_partner=True)._merge(
            partners.ids, self.partner_preserved_id)

        # Archive the partner which is not preserved
        partner_to_archive = partners - self.partner_preserved_id
        partner_to_archive.write({'active': False})

        # Add a message to the chatter
        message = _('Merged into %s') % (self.partner_preserved_id.name)
        partner_to_archive.message_post(body=message)

        # Change duplicate state
        self.write({'state': 'merged'})

    def _find_partner_duplicates(self):
        cr = self.env.cr

        min_similarity = self.env['ir.config_parameter'].get_param(
            'partner_duplicate_mgmt.partner_name_min_similarity')

        cr.execute('SELECT set_limit(%s)', (min_similarity,))
        cr.execute("""
            SELECT p1.id, p2.id
            FROM res_partner p1
            JOIN res_partner p2 ON p1.indexed_name % p2.indexed_name
            WHERE p1.id != p2.id
            AND ((p1.parent_id IS NOT DISTINCT FROM p2.parent_id)
              OR (p1.parent_id IS NULL AND p1.id != p2.parent_id)
              OR (p2.parent_id IS NULL AND p2.id != p1.parent_id)
            )
            AND NOT EXISTS (
                SELECT NULL
                FROM res_partner_duplicate d
                WHERE (d.partner_1_id = p1.id AND d.partner_2_id = p2.id)
                OR    (d.partner_1_id = p2.id AND d.partner_2_id = p1.id)
            )
            """)

        return cr.fetchall()

    def create_duplicates(self):
        res = self._find_partner_duplicates()
        duplicates_sorted = [tuple(sorted(r)) for r in res]
        duplicates = list(set(duplicates_sorted))

        for dup in duplicates:
            self.create({'partner_1_id': dup[0], 'partner_2_id': dup[1]})

    @api.multi
    def action_resolve(self):
        self.filtered(
            lambda x: x.state == 'to_validate').write({
                'state': 'resolved'})

    @api.multi
    def set_to_draft(self):
        self.write({'state': 'to_validate'})

    @api.multi
    def open_partner_merge_wizard(self):
        if len(self) > 1:
            raise UserError(_("Please, select only one duplicate."))

        if self.state != 'to_validate':
            raise UserError(_(
                "You can not merge a line which is not to validate."))

        merge_lines = (
            self.env['res.partner.merge.line'].create_merge_lines(self))
        self.write({'merge_line_ids': [(6, 0, merge_lines.ids)]})

        view = self.env.ref(
            'partner_duplicate_mgmt.res_partner_merge_wizard_form')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_type': 'form',
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'new',
            'res_id': self.id,
        }