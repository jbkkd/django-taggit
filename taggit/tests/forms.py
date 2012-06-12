from django import forms

from taggit.tests.models import (Food, DirectFood, CustomPKFood, OfficialFood,
    CustomUserArticle)


class FoodForm(forms.ModelForm):
    class Meta:
        model = Food

class DirectFoodForm(forms.ModelForm):
    class Meta:
        model = DirectFood

class CustomPKFoodForm(forms.ModelForm):
    class Meta:
        model = CustomPKFood

class OfficialFoodForm(forms.ModelForm):
    class Meta:
        model = OfficialFood

class CustomUserArticleForm(forms.ModelForm):
    class Meta:
        model = CustomUserArticle
