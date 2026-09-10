from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from . import models as m


class AdminChecklistTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('review-admin', 'review@example.com', 'pass')
        self.client.force_login(self.user)
        self.media = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=self.media))

    def image(self, name='logo.png'):
        stream = BytesIO()
        Image.new('RGB', (80, 50), '#d18821').save(stream, 'PNG')
        return SimpleUploadedFile(name, stream.getvalue(), content_type='image/png')

    def test_all_visible_lists_and_add_pages_render(self):
        for model, model_admin in admin.site._registry.items():
            request = self.client.get(reverse('admin:index')).wsgi_request
            if not any(model_admin.get_model_perms(request).values()):
                continue
            opts = model._meta
            for suffix in ('changelist', 'add'):
                if suffix == 'add' and not model_admin.has_add_permission(request):
                    continue
                with self.subTest(model=opts.label, page=suffix):
                    response = self.client.get(reverse(f'admin:{opts.app_label}_{opts.model_name}_{suffix}'), follow=True)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, 'searchCommand()')
                    self.assertNotContains(response, 'name="_continue"')
                    self.assertContains(response, 'custom_admin.css')
                    if suffix == 'add':
                        self.assertNotContains(response, 'class="addlink ')

    def test_logo_crud_and_permissions(self):
        add_url=reverse('admin:content_management_brandlogo_add')
        response=self.client.post(add_url, {'brand_name':'Solene', 'logo':self.image(), 'display_order':'0', 'is_active':'on', '_save':'Save'})
        self.assertEqual(response.status_code,302)
        logo=m.BrandLogo.objects.get()
        self.assertEqual(m.BrandLogoSection.objects.count(),1)
        edit_url=reverse('admin:content_management_brandlogo_change',args=(logo.pk,))
        response=self.client.get(edit_url)
        self.assertContains(response,'This name will be visible on hover in web view.')
        self.assertContains(response,'data-image-preview="logo"')
        self.assertNotIn('alt_text',response.context['adminform'].form.fields)
        response=self.client.post(edit_url, {'brand_name':'Varel', 'display_order':'0','is_active':'on','_save':'Save'})
        self.assertEqual(response.status_code,302)
        logo.refresh_from_db()
        self.assertEqual(logo.alt_text,'Varel')
        response=self.client.get(reverse('admin:content_management_brandlogo_changelist'))
        for text in ('Logo Name','Status','Updated at','Action','Edit','Delete'):
            self.assertContains(response,text)
        delete_url=reverse('admin:content_management_brandlogo_delete',args=(logo.pk,))
        self.assertContains(self.client.get(delete_url),'data-delete-confirmation')
        staff=get_user_model().objects.create_user('reader',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='content_management',codename='view_brandlogo'))
        m.UserRole.objects.update_or_create(user=staff, defaults={"role": m.UserRole.Role.ADMIN})
        self.client.force_login(staff)
        self.assertEqual(self.client.get(add_url).status_code,403)
        self.assertEqual(self.client.get(delete_url).status_code,403)
        self.assertEqual(self.client.post(edit_url, {'brand_name':'Hacked'}).status_code,403)
        response=self.client.get(reverse('admin:content_management_brandlogo_changelist'))
        self.assertNotContains(response,delete_url)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(delete_url,{'post':'yes'}).status_code,302)
        self.assertFalse(m.BrandLogo.objects.exists())

    def test_generic_search_filter_and_sort(self):
        section=m.BrandLogoSection.objects.create()
        m.BrandLogo.objects.create(section=section,brand_name='Zeta',logo='a.png',is_active=True)
        m.BrandLogo.objects.create(section=section,brand_name='Alpha',logo='b.png',is_active=False)
        url=reverse('admin:content_management_brandlogo_changelist')
        response=self.client.get(url, {'q':'alpha'})
        self.assertEqual([obj.brand_name for obj in response.context['cl'].result_list],['Alpha'])
        response=self.client.get(url, {'is_active__exact':'1'})
        self.assertEqual([obj.brand_name for obj in response.context['cl'].result_list],['Zeta'])
        response=self.client.get(url, {'o':'1'})
        self.assertEqual([obj.brand_name for obj in response.context['cl'].result_list],['Alpha','Zeta'])
        self.assertContains(response,'id="admin-sort"')
        self.assertTrue(response.context['admin_sort_options'])

    def test_empty_states_and_independent_permissions(self):
        response=self.client.get(reverse('admin:content_management_giftsection_changelist'))
        self.assertContains(response,'<span>Add Homepage gift section</span>',count=1)
        self.assertNotContains(response,'Create a new item')
        self.assertNotContains(response,'Reset filters')
        response=self.client.get(reverse('admin:auth_user_add'))
        for text in ('>View<','>Add<','>Edit<','>Delete<'):
            self.assertContains(response,text)
        for action in ('view','add','change','delete'):
            perm=Permission.objects.get(content_type__app_label='content_management',codename=f'{action}_brandlogo')
            self.assertContains(response,f'value="{perm.pk}"')
        self.assertNotContains(response,'data-select-access')

    def test_gift_newsletter_and_influencer_forms(self):
        response=self.client.get(reverse('admin:content_management_giftsection_add'))
        self.assertContains(response,'id="gift-live-preview"')
        self.assertContains(response,'data-gift-statistics')
        self.assertContains(response,'data-gift-features')
        response=self.client.get(reverse('admin:content_management_newslettersettings_add'))
        self.assertEqual(set(response.context['adminform'].form.fields),{'title','description'})
        get_user_model().objects.create_user('UniqueCreator')
        from .admin import InfluencerAdmin
        form=InfluencerAdmin.form(data={'username':'uniquecreator'})
        form.is_valid()
        self.assertIn('username',form.errors)

    def test_render_review_artifacts(self):
        import os, re
        from django.contrib.staticfiles import finders
        destination=os.environ.get('ADMIN_REVIEW_ARTIFACTS')
        if not destination:
            self.skipTest('Optional browser review artifacts')
        root=Path(destination); root.mkdir(parents=True,exist_ok=True)
        section=m.BrandLogoSection.objects.create()
        logo=m.BrandLogo.objects.create(section=section,brand_name='Solene',logo=self.image())
        banner=m.Banner.objects.create(title='Crafted for your grandest moments',image=self.image('hero.png'))
        gift=m.GiftSection.objects.create(main_image=self.image('gift.png'))
        for index in range(5):
            m.GiftSectionFeature.objects.create(section=gift,text=f'Gift feature {index+1}')
        for index in range(3):
            m.GiftSectionStatistic.objects.create(section=gift,value=f'{index+1}K+',label='Happy customers')
        (root/'catalog.js').write_bytes(self.client.get(reverse('admin:jsi18n')).content)
        pages={
            'brand-list':reverse('admin:content_management_brandlogo_changelist'),
            'brand-edit':reverse('admin:content_management_brandlogo_change',args=(logo.pk,)),
            'staff-add':reverse('admin:auth_user_add'),
            'gift-edit':reverse('admin:content_management_giftsection_change',args=(gift.pk,)),
            'banner-edit':reverse('admin:content_management_banner_change',args=(banner.pk,)),
        }
        for name,url in pages.items():
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            html=response.content.decode()
            html=re.sub(r'(["\'])/static/([^"\']+)',lambda match: match[1]+'file://'+str(finders.find(match[2]) or match[2]),html)
            html=html.replace('/admin/jsi18n/',f'file://{root}/catalog.js')
            # Copy temporary uploaded files for the standalone preview pages.
            import shutil
            for file in Path(self.media).rglob('*'):
                if file.is_file():
                    target=root/'media'/file.relative_to(self.media);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
            html=html.replace('/media/',f'file://{root}/media/')
            (root/f'{name}.html').write_text(html)
