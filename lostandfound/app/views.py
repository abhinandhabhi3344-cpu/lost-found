from django.shortcuts import render

# Create your views here.

def home(request):
    return render(request, 'home.html')

def userdash(request):
    return render(request, 'user-dash.html')

def dictativepro(request):
    return render(request, 'dictative-profile-view.html')

def dictdash(request):
    return render(request, 'dictative-dash.html')

def case(request):
    return render(request, 'case.html')

def casedetails(request):
    return render(request, 'case-details.html')

def admindash(request):
    return render(request, 'admin-dash.html')

def dictatives(request):
    return render(request, 'dictatives.html')


